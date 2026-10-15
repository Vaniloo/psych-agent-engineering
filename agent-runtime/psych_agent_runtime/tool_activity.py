"""Small public activity summaries, without raw arguments or tool responses."""

from __future__ import annotations

PUBLIC_TOOLS = {
    "search_knowledge": "检索心理支持知识",
    "memory_search": "查找相关记忆",
    "get_goal_context": "读取我的目标",
    "get_dashboard": "读取心理概要",
    "get_support_resources": "查找支持资源",
    "crisis_support": "读取紧急支持资源",
    "create_goal": "创建目标与小步骤",
    "sync_goal_progress": "同步目标进展",
}


def result_summary(name: str, result) -> str:
    data = result if isinstance(result, dict) else {}

    def count(value):
        return len(value) if isinstance(value, list) else 0

    if isinstance(result, dict) and result.get("unavailable"):
        return "暂时无法取得结果，回复会说明这一限制"
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
    if name == "get_goal_context":
        size = count(data.get("goals"))
        return f"读取到 {size} 个目标" if size else "目前没有已保存的目标"
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
    if name == "create_goal":
        if data.get("created") is not True:
            return "本次未创建目标"
        goal = data.get("goal")
        actions = goal.get("actions") if isinstance(goal, dict) else []
        size = count(actions)
        return f"已创建目标，包含 {size} 个小步骤" if size else "已创建目标"
    if name == "sync_goal_progress":
        return "已保存这次行动进展" if data.get("applied") is True else "本次未更新目标进展"
    return "调用已完成"
