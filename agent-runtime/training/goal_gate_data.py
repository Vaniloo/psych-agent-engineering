"""Versioned contextual goal curriculum; authored labels, no private chats/API calls."""

import argparse
import hashlib
import json
import random
from pathlib import Path

from psych_agent_runtime.goal_gate_contract import CONTRACT, INSTRUCTIONS, normalize


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def scenarios(topic, variant):
    goal = f"每天学一点{topic}"
    first = [f"确定今天的{topic}小主题", f"打开{topic}教材的一页", f"选一个{topic}示例"][variant]
    second = f"学习{topic}并记下一句要点"
    habits = {
        "作息": ("每天睡够八小时", "确定一个合适的入睡时间", "起床后记录实际睡眠时长"),
        "饮食": ("慢慢改善饮食", "选择一个容易调整的饮食点", "记录一餐吃了什么"),
        "散步": ("每天散步一会儿", "穿上适合走路的鞋", "走五分钟并记录感受"),
        "面试": ("准备一次面试", "选一道面试题", "练习回答并记录卡点"),
        "整理": ("让房间整洁一点", "选一小块要整理的地方", "整理这块地方并记录感受"),
        "联系": ("恢复一些社交联系", "选择一位想联系的朋友", "发一句问候并记录感受"),
        "写日记": ("每天写一点日记", "打开日记本", "写一句今天的感受"),
        "休息": ("给自己留休息时间", "确定一个可以暂停的时段", "休息几分钟并记录感受"),
    }
    if topic in habits:
        goal, first, second = habits[topic]
        first = [first, "先" + first, "今天先" + first][variant]
    candidates = [{"goalTitle": goal, "actionTitle": title, "position": i + 1,
                   "focused": True, "status": "planned"} for i, title in enumerate([first, second])]
    history = [{"role": "user", "content": f"设置一个{goal}的目标"},
               {"role": "assistant", "content": f"已创建目标「{goal}」。1. {first}。2. {second}。"}]
    cases = []

    def add(name, message, labels=(), target=None, context=None, pool=None):
        cases.append({"name": name, "state": {"message": message,
                      "history": history if context is None else context,
                      "candidates": candidates if pool is None else pool},
                      "positive": list(labels), "target": target})

    add("create", f"请帮我设置一个{goal}的目标", ("goal_related", "create_request"), context=[])
    add("adopt", "那就把这个加入我的目标吧", ("goal_related", "create_request"), context=[
        {"role": "user", "content": f"我希望{goal}，刚才还没要你保存"},
        {"role": "assistant", "content": f"可以从{first}开始。"}])
    add("read", "第一个小步骤是什么", ("goal_related",))
    add("numbered", "OK，小步骤1我做完了", ("goal_related", "progress_report", "outcome_done"), 0)
    add("second", "第二步已经完成了", ("goal_related", "progress_report", "outcome_done"), 1)
    add("implicit", "刚才说的那个，我已经搞定啦", ("goal_related", "progress_report", "outcome_done"), 0,
        context=[*history, {"role": "user", "content": "第一步是什么来着"},
                 {"role": "assistant", "content": first}])
    add("literal", f"我今天已经{first}了", ("goal_related", "progress_report", "outcome_done"), 0)
    add("partial", "第一步试了一点，还没有全部做完", ("goal_related", "progress_report", "outcome_partial"), 0)
    add("blocked", "第一步卡住了，我现在不知道怎么继续", ("goal_related", "progress_report", "outcome_blocked"), 0)
    add("rest", "第一步这次先休息，今天不继续了", ("goal_related", "progress_report", "outcome_rest"), 0)
    add("future", "第一步我打算明天做完", ("goal_related",))
    add("denied", "第一步我还没动，什么都没做", ("goal_related",))
    add("other", "小步骤1我朋友做完了，我没有做", ("goal_related",))
    add("quote", "解释这句话：“第一步我做完了”", ("goal_related",))
    add("optout", "第一步我完成了，但是不要同步或更新目标", ("goal_related",))
    add("question", "第一步我完成了吗？", ("goal_related",))
    add("old", "上次第一步已经记过完成，我只是回顾旧记录，别再更新", ("goal_related",))
    add("unrelated", "今晚天气怎么样，随便聊两句", (), context=[])
    ambiguous = [{**c, "focused": False} for c in candidates]
    ambiguous.append({**ambiguous[0], "goalTitle": "另一个不同目标"})
    add("ambiguous", "第一个我完成了", ("goal_related", "progress_report", "outcome_done"),
        context=[{"role": "assistant", "content": "有两个目标，各有第一步，你想聊哪个？"}], pool=ambiguous)
    return cases


def build(out):
    if out.exists():
        raise ValueError("refuse to overwrite frozen goal gate course")
    out.mkdir(parents=True)
    topics = ["机器学习", "线性代数", "英语语法", "摄影构图", "数据结构", "Python", "素描", "时间管理",
              "法语", "世界历史", "概率论", "产品设计", "写作", "吉他", "烘焙", "算法", "统计学", "围棋",
              "数据库", "日语", "园艺", "天文学", "经济学", "书法", "剪辑", "色彩", "音乐理论", "几何",
              "阅读理解", "公共表达", "逻辑学", "德语", "动画", "水彩", "网络基础", "陶艺", "演讲", "速写",
              "作息", "饮食", "散步", "面试", "整理", "联系", "写日记", "休息"]
    families = [(t, v) for t in topics for v in range(3)]
    random.Random(1721).shuffle(families)
    spans = {"train": families[:32], "calibration": families[32:56],
             "validation": families[56:80], "test": families[80:104]}
    manifest = {"contractVersion": CONTRACT, "instructionsSha256": digest(INSTRUCTIONS),
                "annotationStatus": "programmatic_development_not_independent_human_gold",
                "splitPolicy": "disjoint_topic_and_step_variant_families_shared_templates",
                "hashes": {}, "rows": {}}
    for split, groups in spans.items():
        rows = []
        for topic, variant in groups:
            family = digest([topic, variant])
            for case in scenarios(topic, variant):
                for task in INSTRUCTIONS:
                    indices = range(len(case["state"]["candidates"])) if task == "candidate_match" else [None]
                    for index in indices:
                        state = {**case["state"], **({"candidateIndex": index} if index is not None else {})}
                        normalize(task, state)
                        positive = index == case["target"] if task == "candidate_match" else task in case["positive"]
                        rows.append({"task": task, "state": state, "label": "yes" if positive else "no",
                                     "family": family, "scenario": case["name"], "split": split})
        random.Random(1721).shuffle(rows)
        raw = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()
        (out / f"{split}.jsonl").write_bytes(raw)
        manifest["hashes"][split] = hashlib.sha256(raw).hexdigest()
        manifest["rows"][split] = len(rows)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().out), ensure_ascii=False))
