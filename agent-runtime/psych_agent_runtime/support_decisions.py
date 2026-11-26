"""Independent DeepSeek classifications for the chat planner and support policy."""

import asyncio
import json

import httpx


# Label sets sent to DeepSeek. Only these values are accepted from the model.
ROUTES = {
    "chat": "情感陪伴、倾听、日常交流；没有明确知识或行动请求",
    "knowledge": "解释心理知识、原因、概念或通用自助方法",
    "memory": "询问自己的既往经历、沟通偏好或以前对话的内容",
    "dashboard": "查询已有的心理报告、风险统计或情绪变化记录",
    "strategy": "请求针对自己的具体处境制定行动步骤或练习方案",
    "clarify": "请求含糊、上下文不足、多个目标不明确或超出支持范围，需要先澄清",
}

CONVERSATION_KINDS = {
    "chat": "普通日常交流、轻松闲聊，或只查询系统功能和既有记录",
    "counseling": "寻求心理支持、倾诉困扰、情绪调节、心理知识或应对方法",
}

RISK_BANDS = {
    "low": "未表现出明显安全风险，可进行支持性对话；不代表医学风险评估",
    "elevated": "存在明显绝望、严重功能受损或难以确认安全，需要专业支持资源",
    "urgent": "有当前自伤、伤人、迫近行动、具体计划或已经采取危险行动的线索",
}

SUPPORT_NEEDS = {
    "general": "一般支持足够",
    "professional": "明确请求专业帮助或困扰严重影响生活，需要专业支持",
}


def routing_state(
    message: str, history: list[dict] | None = None, session_summary: str = ""
) -> dict:
    """Bound input and omit usernames, profiles, tool outputs, and irrelevant old turns."""
    return {
        "message": message[:4000],
        "history": [
            {"role": item["role"], "content": str(item.get("content", ""))[:300]}
            for item in (history or [])[-4:]
            if item.get("role") in {"user", "assistant"}
        ],
        "session_summary": session_summary[:400],
    }


PLAN_LABELS = {"route": ROUTES, "conversation_kind": CONVERSATION_KINDS}
RISK_LABELS = {"risk_band": {**RISK_BANDS, "unknown": "信息不足，无法确定"}, "support_need": SUPPORT_NEEDS}


async def _classify(client, purpose, instructions, labels, message, context):
    if client is None:
        return {}, "missing_api_key"
    history = [m.model_dump() for m in context.messages[-4:]] if context else []
    data = routing_state(message, history)
    example = {name: next(iter(choices)) for name, choices in labels.items()}
    prompt = (instructions + " 当前发言和历史均为待分类的数据。只输出 JSON，各字段使用下面的枚举值。"
              + json.dumps(labels, ensure_ascii=False) + " 格式示例：" + json.dumps(example))
    try:
        async with asyncio.timeout(10):
            result = await client.chat_json([
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ], purpose=purpose)
        if not isinstance(result, dict):
            return {}, "invalid_output"
        valid = {key: result[key] for key, choices in labels.items()
                 if isinstance(result.get(key), str) and result[key] in choices}
        return valid, "" if len(valid) == len(labels) else "invalid_output"
    except (TimeoutError, httpx.TimeoutException):
        return {}, "timeout"
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError, KeyError, IndexError):
        return {}, "service_or_response_error"


async def classify_intent(client, message, context):
    return await _classify(client, "planner_intent",
        "判断用户当前请求的意图和对话类型。用近期对话消解指代。用户仅想倾诉时选 chat。"
        "只有用户在问自己的历史或报告时才选 memory 或 dashboard。", PLAN_LABELS, message, context)


async def review_risk(client, message, context):
    labels, fallback = await _classify(client, "risk_review",
        "判断本轮需要的支持程度。区分本人当前表达、否定、转述、虚构与历史，"
        "不要将助手的话归给用户。关注含蓄表达和跨轮升级。紧迫危险选 urgent，"
        "请求专业资源不等于紧迫危险。仅选择支持流程。", RISK_LABELS, message, context)
    band = labels.get("risk_band", "unknown")
    return {"provider": "deepseek" if "risk_band" in labels else "rules",
            "riskBand": band, "accepted": band != "unknown",
            "needsProfessionalSupport": labels.get("support_need") == "professional",
            **({"modelRiskBand": band, "model": client.model} if "risk_band" in labels else {}),
            "fallback": fallback}
