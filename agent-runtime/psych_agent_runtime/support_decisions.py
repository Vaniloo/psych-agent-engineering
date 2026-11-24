"""Independent DeepSeek classifications for the chat planner and support policy."""

import asyncio
import json

import httpx

from .decision_contract import CONVERSATION_KINDS, RISK_BANDS, SUPPORT_NEEDS
from .routing_contract import ROUTES, routing_state

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
