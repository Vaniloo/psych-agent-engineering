"""Small public activity summaries, without raw arguments or tool responses."""

from __future__ import annotations

PUBLIC_TOOLS = {
    "search_web": "搜索网络",
    "search_role_voice": "查找角色语气参考",
    "search_knowledge": "检索心理支持知识",
    "memory_search": "查找相关记忆",
    "get_dashboard": "读取心理概要",
    "get_support_resources": "查找支持资源",
    "crisis_support": "读取紧急支持资源",
}


def result_summary(name: str, result) -> str:
    data = result if isinstance(result, dict) else {}

    def count(value):
        return len(value) if isinstance(value, list) else 0

    if isinstance(result, dict) and result.get("unavailable"):
        return "暂时无法取得结果，回复会说明这一限制"
    if name in {"search_web", "search_role_voice"}:
        if data.get("available") is not True:
            return "网络搜索暂不可用"
        if data.get("reason") == "no_results":
            return "没有找到可引用的网页来源"
        kind = "相关表达参考" if name == "search_role_voice" else "网页来源"
        return f"{'复用缓存，' if data.get('cacheHit') else ''}找到 {count(data.get('results'))} 条{kind}"
    if name == "search_knowledge":
        size = count(data.get("hits"))
        return f"找到 {size} 条可用知识片段" if size else "没有找到可用知识片段"
    if name == "memory_search":
        size = count(data.get("evidence"))
        if data.get("purpose") == "context":
            if data.get("status") == "unavailable":
                return "跨会话背景暂不可用，继续当前对话"
            return f"补充 {size} 条其他会话的相关原话" if size else "未补充历史，继续当前对话"
        if not size:
            if pending := count(data.get("candidateEvidence")):
                return f"找到 {pending} 条待核对原话"
            return "这次没有找到相关原话"
        if data.get("status") == "lexical_fallback":
            return f"读取到 {size} 条候选原话，使用检索兜底"
        limited = bool((data.get("retrievalCoverage") or {}).get("missing"))
        return f"找到 {size} 条相关原话" + ("，部分信息仍未找到依据" if limited else "")
    if name == "get_dashboard":
        availability = data.get("reportAvailability") or {}
        if availability.get("hasConversationOverview"):
            return "已读取近期对话概览" + (
                "，记录仍较有限" if availability.get("overviewLimited") else ""
            )
        if availability.get("answerKind") == "empty":
            return "目前没有可看的报告或对话概览"
        return "已读取当前概要和实际记录"
    if name == "get_support_resources":
        size = count(result)
        return f"找到 {size} 项支持资源" if size else "目前没有可用支持资源"
    if name == "crisis_support":
        return "已读取紧急支持回复与资源"
    return "调用已完成"
