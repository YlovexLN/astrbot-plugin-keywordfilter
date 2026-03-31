from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, sp
from astrbot.api.message_components import Reply, Plain
import re

@register("astrbot_plugin_keywordfilter", "YlovexLN", "关键词拦截插件，支持正则、完全匹配、关键词匹配，防止触发 AI 大模型，支持分群/私聊单独配置。", "0.0.1")
class KeywordFilterPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        # 从 sp 获取本地存储的规则: {session_id: [rules]}
        self.local_rules = sp.get("local_keyword_rules", {})

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def interceptor(self, event: AstrMessageEvent):
        """核心拦截器逻辑"""
        message_str = event.message_str
        if not message_str:
            return

        message_chain = event.get_messages()
        is_quote = any(isinstance(c, Reply) for c in message_chain)
        session_id = event.get_session_id()
        
        # 1. 检查全局规则 (WebUI 配置)
        global_rules = self.config.get("rules", [])
        for rule in global_rules:
            if self._check_match(rule, message_str, is_quote, session_id, is_global=True):
                logger.info(f"[KeywordFilter] 匹配到全局拦截规则 ({rule.get('match_type')}): '{rule.get('pattern')}'，会话: {session_id}")
                event.stop_event()
                return

        # 2. 检查本地规则 (指令配置)
        chat_rules = self.local_rules.get(session_id, [])
        for rule in chat_rules:
            if self._check_match(rule, message_str, is_quote, session_id, is_global=False):
                logger.info(f"[KeywordFilter] 匹配到本地拦截规则 ({rule.get('match_type')}): '{rule.get('pattern')}'，会话: {session_id}")
                event.stop_event()
                return

    def _check_match(self, rule, message_str, is_quote, session_id, is_global=True):
        """单条规则匹配检测"""
        if not rule.get("enabled", True):
            return False
        
        pattern = rule.get("pattern")
        match_type = rule.get("match_type", "keyword")
        if not pattern:
            return False
        
        # 检查匹配模式
        matched = False
        if match_type == "keyword":
            matched = pattern in message_str
        elif match_type == "exact":
            matched = pattern == message_str
        elif match_type == "regex":
            try:
                matched = bool(re.search(pattern, message_str))
            except Exception as e:
                logger.error(f"[KeywordFilter] 正则表达式 '{pattern}' 错误: {e}")
                return False
        
        if not matched:
            return False
            
        # 检查引用回复限制
        if rule.get("intercept_quote_only", False) and not is_quote:
            return False
        
        # 检查全局规则的生效范围
        if is_global:
            apply_to_all = rule.get("apply_to_all", True)
            target_chats = rule.get("target_chats", [])
            if not apply_to_all and session_id not in target_chats:
                return False
        
        return True

    @filter.command("kwf")
    async def kwf_cmd(self, event: AstrMessageEvent):
        """关键词拦截管理系统 (/kwf)"""
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result(
                "--- 关键词拦截管理 (/kwf) ---\n"
                "1. /kwf list - 查看当前会话生效规则\n"
                "2. /kwf add <模式> <内容> [仅引用: true/false]\n"
                "   模式支持: keyword(包含), exact(全匹配), regex(正则)\n"
                "3. /kwf del <内容> - 删除当前会话规则"
            )
            return

        cmd = parts[1].lower()
        session_id = event.get_session_id()

        if cmd == "list":
            # 聚合显示规则
            global_rules = [r for r in self.config.get("rules", []) if self._check_match(r, r.get('pattern'), False, session_id, is_global=True) or (r.get('apply_to_all') or session_id in r.get('target_chats', []))]
            local_rules = self.local_rules.get(session_id, [])
            
            if not global_rules and not local_rules:
                yield event.plain_result(f"当前会话 ({session_id}) 没有任何拦截规则。")
                return
            
            res = f"--- 会话 {session_id} 规则列表 ---\n"
            if global_rules:
                res += "【全局规则】:\n"
                for r in global_rules:
                    q = "(仅引用)" if r.get("intercept_quote_only") else ""
                    res += f"- [{r.get('match_type')}] {r.get('pattern')} {q}\n"
            if local_rules:
                res += "【本地规则】:\n"
                for r in local_rules:
                    q = "(仅引用)" if r.get("intercept_quote_only") else ""
                    res += f"- [{r.get('match_type')}] {r.get('pattern')} {q}\n"
            yield event.plain_result(res.strip())

        elif cmd == "add":
            if len(parts) < 4:
                yield event.plain_result("格式错误。用法: /kwf add <模式: keyword/exact/regex> <内容> [仅引用: true/false]")
                return
            
            m_type = parts[2].lower()
            if m_type not in ["keyword", "exact", "regex"]:
                yield event.plain_result("无效模式，请使用: keyword, exact 或 regex。")
                return
            
            pattern = parts[3]
            quote_only = parts[4].lower() == "true" if len(parts) > 4 else False
            
            if session_id not in self.local_rules:
                self.local_rules[session_id] = []
            
            # 避免重复
            for r in self.local_rules[session_id]:
                if r['pattern'] == pattern and r['match_type'] == m_type:
                    yield event.plain_result(f"规则 '{pattern}' ({m_type}) 已存在。")
                    return
            
            self.local_rules[session_id].append({
                "pattern": pattern,
                "match_type": m_type,
                "intercept_quote_only": quote_only,
                "enabled": True
            })
            sp.put("local_keyword_rules", self.local_rules)
            yield event.plain_result(f"已添加规则: [{m_type}] {pattern} {'(仅引用)' if quote_only else ''}")

        elif cmd == "del":
            if len(parts) < 3:
                yield event.plain_result("请输入要删除的内容。")
                return
            
            pattern = parts[2]
            if session_id not in self.local_rules:
                yield event.plain_result("当前会话没有本地规则。")
                return
            
            orig_len = len(self.local_rules[session_id])
            self.local_rules[session_id] = [r for r in self.local_rules[session_id] if r['pattern'] != pattern]
            
            if len(self.local_rules[session_id]) < orig_len:
                sp.put("local_keyword_rules", self.local_rules)
                yield event.plain_result(f"已删除本地规则: {pattern}")
            else:
                yield event.plain_result(f"未找到匹配的本地规则: {pattern}")

    async def terminate(self):
        pass
