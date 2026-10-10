"""Bootstrap data only: authored synthetic scenarios, never real patient records.

Variants of a scenario always stay in one split. Test cases are separate authored
utterances, not random rows from the augmentation pool. This is NOT a clinical
dataset or evidence of production generalization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from psych_agent_runtime.routing_contract import CONTRACT_VERSION, QUESTIONS, routing_state

# Each entry is a distinct scenario family; the split is made BEFORE expansion.
SCENARIOS = {
    "chat": [
        "今天有点累，想找个人说说话",
        "我不需要建议，你听我说就好",
        "终于把工作做完了，来分享一下",
        "最近心里闷闷的，只想聊聊",
        "刚和朋友吵了架，我现在很委屈",
        "你好，很高兴见到你",
        "今天的晚霞很好看",
        "我有点孤单，可以陪我一会儿吗",
        "你今天怎么样呀",
        "我被老板批评了，感觉很难受",
        "这次考试没考好，先让我抱怨一下",
        "昨天睡得不好，今天好困",
        "我很紧张，暂时不想做什么练习",
        "谢谢你刚才听我说这些",
        "终于放假了，心情特别好",
        "只是想有人理解我，不用分析原因",
        "晚上一个人在家，想聊聊天",
        "朋友爽约让我有点失望",
        "工作太忙了，我想吐槽两句",
        "不想立刻解决问题，先陪陪我",
        "今天给自己做了一顿饭",
        "我现在没什么问题，就是来打个招呼",
        "刚哭了一场，想有人听我说",
        "这一周很不容易，想聊聊发生的事",
    ],
    "knowledge": [
        "焦虑通常有哪些表现",
        "什么是睡眠卫生",
        "为什么紧张时会心跳加快",
        "请解释认知行为疗法的基本思路",
        "什么叫情绪命名",
        "呼吸练习为什么能帮助放松",
        "压力和焦虑有什么不同",
        "拖延常见的心理原因是什么",
        "什么是行为激活",
        "正念和冥想是一回事吗",
        "解释一下反刍思维",
        "失眠和睡前刷手机有什么关系",
        "什么叫完美主义",
        "社交焦虑和内向有什么区别",
        "地面化练习的原理是什么",
        "为什么运动会影响情绪",
        "什么是渐进式肌肉放松",
        "心理咨询通常如何开展",
        "请介绍常见的压力调节方法",
        "什么是自我关怀",
        "惊恐发作是什么意思",
        "为什么越担心睡不着越难入睡",
        "给我科普一下情绪和身体反应的联系",
        "自动化思维是什么意思",
    ],
    "memory": [
        "你还记得我之前说过什么吗",
        "我以前提过哪些烦恼",
        "我喜欢哪种沟通方式",
        "上次我说考试在什么时候",
        "之前聊到的朋友叫什么",
        "我告诉过你什么有效的放松方式",
        "我以前说过不喜欢什么建议",
        "你记得我的支持目标吗",
        "帮我回忆上一次我们聊了什么",
        "我曾经提过什么工作压力",
        "我之前讲过自己的睡眠习惯吗",
        "你知道我的沟通偏好吗",
        "我以前说过什么会让我舒服一点",
        "上回说的那件事情你记得吗",
        "你对我的了解有哪些",
        "我之前有没有说过自己养了宠物",
        "我上次提到的计划是什么",
        "我们以前聊过哪些应对方法",
        "我曾经说过想改变什么",
        "你记得我不想被叫的称呼吗",
        "以前的对话里，我说自己在担心什么",
        "请回顾一下我告诉过你的个人偏好",
        "我上一次谈起的家庭情况是什么",
        "我说过最希望得到哪种支持吗",
    ],
    "dashboard": [
        "查看我最近的心理报告",
        "我的情绪趋势是什么样的",
        "统计一下我这个月的风险记录",
        "最近有多少条高风险报告",
        "展示我的dashboard",
        "我过去三十天的情绪有变化吗",
        "帮我汇总最近保存的分析结果",
        "我最近一次报告是什么风险等级",
        "统计已有记录中的焦虑次数",
        "帮我查看心理报告的历史数据",
        "看一下最近的风险分布",
        "我的报告里最常见的情绪是什么",
        "我这周有几条心理分析记录",
        "显示上个月的情绪统计",
        "查看我的历史风险等级",
        "汇总已有的心理状态报告",
        "我的情绪记录最近是在改善吗",
        "给我看看个人数据看板",
        "报告中低风险和中风险各有多少",
        "帮我查看最近保存的情绪分析",
        "我最新的评估记录是什么",
        "查询我最近的心理状态统计",
        "从已有记录里看一下情绪变化",
        "最近一个月的报告总数是多少",
    ],
    "strategy": [
        "我今晚又睡不着，帮我安排具体的睡前步骤",
        "明天要考试，我很紧张，给我一个能执行的方案",
        "我总是不敢开始写论文，帮我拆成小步骤",
        "刚跟家人争吵了，我现在能做点什么",
        "我工作压力很大，请按我的情况给三个建议",
        "我想练习应对社交焦虑，帮我制定计划",
        "每天都拖到很晚才睡，帮我做个调整作息的计划",
        "帮我安排一个今天就能做的放松练习",
        "我怕明天的演讲，请陪我制定准备步骤",
        "我想开始运动改善情绪，帮我安排本周的行动",
        "我一到周日晚上就担心工作，请给我具体办法",
        "我总是反复检查作业，帮我设计减少检查的步骤",
        "我想和朋友修复关系，帮我想一个开口的方式",
        "我这周任务很多，帮我排一个减压行动清单",
        "我因为搬家很焦虑，今天先做什么比较好",
        "我想试试地面化练习，请带我一步步做",
        "我害怕接电话，帮我设计从简单开始的练习",
        "我想改善睡前刷手机的习惯，帮我做计划",
        "我总是对自己很苛刻，请给我今天可做的练习",
        "请围绕我失眠的情况制定三天的尝试方案",
        "上班前我总会很慌，请给我能照着做的步骤",
        "我不敢提交作品，帮我安排第一步",
        "我想试着管理自己的压力，今天可以怎样开始",
        "请根据我最近的困扰给一个可执行的小目标",
    ],
    "clarify": [
        "帮我处理一下",
        "就那个事情",
        "你知道我指的是什么吧",
        "帮我弄好就行",
        "来一个吧",
        "继续",
        "怎么办",
        "我不知道该问什么",
        "那个给我看看",
        "处理之前那个",
        "帮我决定",
        "说点有用的",
        "好像不太对",
        "这个怎么弄",
        "换一个",
        "就照之前的来",
        "把它改掉",
        "帮我做一下那个",
        "你来安排吧",
        "请给我开个药方",
        "帮我诊断一下具体得了什么病",
        "帮我操作股票账户买入",
        "替我登录别人的账号",
        "请替我做个决定，但我还没说是什么事",
    ],
}
TEST_CASES = {
    "chat": [
        "我只想吐槽失眠，不要给方法",
        "拿到报告后我很难过，先别分析，陪我聊聊",
        "今天心情糟糕，但不想听建议",
        "多谢，有你听着我好受了一点",
        "刚刚看完电影，很想跟人分享",
        "我只是有点紧张，想被理解",
    ],
    "knowledge": [
        "请讲讲睡眠驱动力这个概念",
        "社交时为什么会脸红",
        "解释一下什么叫情绪调节",
        "通常怎样理解压力反应",
        "一般的焦虑与惊恐有何区别",
        "说说呼吸练习背后的机制",
    ],
    "memory": [
        "我当时说喜欢直接还是委婉的表达",
        "你记得我提到的那个长期目标吗",
        "回忆一下之前我分享的经历",
        "我上回是不是提到换工作",
        "我提过哪些不适合我的练习",
        "你还记得我最近在忙什么吗",
    ],
    "dashboard": [
        "打开已经生成的心理评估汇总",
        "我存下来的报告最近有什么变化",
        "上周记录中的风险比例是多少",
        "从历史记录看看我的情绪分布",
        "把我个人报告的统计给我看一下",
        "最近生成了几份状态分析",
    ],
    "strategy": [
        "我下周要答辩，请帮我列准备时缓解紧张的步骤",
        "我又拖着不写作业，陪我确定现在的第一步",
        "针对我睡前忍不住看手机的情况，安排一个小计划",
        "请带着我实际做一次放松",
        "想减少和伴侣争执，帮我想今晚怎么沟通",
        "明早要面试，请给我今晚可以照做的安排",
    ],
    "clarify": [
        "弄一下那个就好",
        "你直接做吧",
        "把我想要的告诉我",
        "我也没确定想让你做什么",
        "替我确诊是不是有某种疾病",
        "帮我下一个投资指令",
    ],
}


def row(message: str, label: str, scenario_id: str, history=None) -> dict:
    return {
        "state": routing_state(message, history),
        "questions": QUESTIONS,
        "expected": {"route": label},
        "scenario_id": scenario_id,
        "provenance": "authored_synthetic_bootstrap",
    }


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    parts = {name: [] for name in ("train", "calibration", "validation", "test")}
    prefixes = ["", "你好，", "我想问问，", "现在有空吗，", "请帮个忙，", "我有个请求："]
    suffixes = ["", "。", "，谢谢", "，可以吗", "，请用中文回答", "。只需要处理我这次的请求"]
    distractions = [
        None,
        [
            {"role": "user", "content": "昨天我们聊过睡眠"},
            {"role": "assistant", "content": "今天你想聊什么？"},
        ],
    ]
    for label, seeds in SCENARIOS.items():
        for i, message in enumerate(seeds):
            split = "train" if i < 16 else "calibration" if i < 20 else "validation"
            sid = f"{label}-{i}"
            for prefix in prefixes:
                for suffix in suffixes:
                    # Distractions force the latest explicit request to win.
                    history = distractions[(len(prefix) + len(suffix)) % 2]
                    parts[split].append(row(prefix + message + suffix, label, sid, history))
        for i, message in enumerate(TEST_CASES[label]):
            parts["test"].append(row(message, label, f"test-{label}-{i}"))
    # Context-dependent follow-ups, grouped separately from standalone requests.
    contexts = {
        "knowledge": ("我想了解认知重评的含义", "能再解释一下吗"),
        "dashboard": ("展示我的情绪统计数据", "也看看最近一个月的"),
        "memory": ("回忆我过去告诉你的沟通偏好", "还有我说过的目标呢"),
        "strategy": ("帮我设计缓解面试紧张的行动计划", "把第一步再拆小一点"),
        "chat": ("我只需要你听着，不要给建议", "我们继续聊吧"),
    }
    for label, (previous, message) in contexts.items():
        parts["test"].append(
            row(
                message,
                label,
                f"test-context-{label}",
                [
                    {"role": "user", "content": previous},
                    {"role": "assistant", "content": "好的，我们继续。"},
                ],
            )
        )
    rng = random.Random(20261009)
    manifest = {"contractVersion": CONTRACT_VERSION, "scope": "synthetic_bootstrap", "splits": {}}
    signatures = set()
    for name, rows in parts.items():
        rng.shuffle(rows)
        path = out / f"{name}.jsonl"
        payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows)
        path.write_text(payload, encoding="utf-8")
        for item in rows:
            signature = json.dumps(item["state"], ensure_ascii=False, sort_keys=True)
            if signature in signatures:
                raise ValueError("duplicate input across generated data")
            signatures.add(signature)
        manifest["splits"][name] = {
            "rows": len(rows),
            "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    build(parser.parse_args().out)
