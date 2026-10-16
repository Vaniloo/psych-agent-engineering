"""Small authored assertion curriculum; no private chats, teacher API or clinical labels."""

import argparse
import hashlib
import json
import random
from pathlib import Path

from benchmarks.memory_fact_gate import ASSERTION_ZH

# Each attribute family stays in a single partition. These are authored synthetic
# examples, not independent human annotations or evidence of real-world accuracy.
FAMILIES = {
    "music": [f"喜欢听{x}" for x in ("爵士乐", "钢琴曲", "民谣", "古典音乐", "摇滚", "轻音乐")],
    "food": [f"不吃{x}" for x in ("香菜", "花生", "辣椒", "海鲜", "生姜", "肥肉")],
    "city": [f"在{x}工作" for x in ("杭州", "上海", "广州", "成都", "苏州", "武汉")],
    "pet": [f"养了一只叫{x}的猫" for x in ("小米", "团子", "豆豆", "雪球", "花卷", "芝麻")],
    "job": [f"做{x}已经三年了" for x in ("设计", "编辑", "销售", "会计", "开发", "教师")],
    "sleep": [f"最近常在{x}才睡着" for x in ("凌晨一点", "凌晨两点", "半夜", "十一点", "十二点", "一点半")],
    "commute": [f"每天坐{x}上班" for x in ("公交", "地铁", "班车", "轮渡", "电车", "城际列车")],
    "emotion": [f"今天感到{x}" for x in ("开心", "疲惫", "紧张", "平静", "失落", "烦躁")],
    "hobby": [f"平时喜欢{x}" for x in ("画画", "游泳", "跑步", "摄影", "园艺", "下棋")],
    "family": [f"和{x}住在一起" for x in ("爸妈", "姐姐", "弟弟", "祖母", "伴侣", "表妹")],
    "study": [f"正在学{x}" for x in ("日语", "法语", "吉他", "编程", "绘画", "烘焙")],
    "routine": [f"每天早上{x}起床" for x in ("六点", "七点", "八点", "五点半", "六点半", "七点半")],
    "achievement": [f"上周完成了{x}" for x in ("演讲", "面试", "考试", "搬家", "项目", "长跑")],
    "pressure": [f"一遇到{x}就容易紧张" for x in ("公开发言", "考试", "陌生人", "截止日期", "争执", "聚会")],
    "health_state": [f"最近常常{x}" for x in ("肩膀酸", "眼睛累", "走神", "没胃口", "容易困", "忘记喝水")],
    "quiet": [f"更喜欢{x}的环境" for x in ("安静", "明亮", "开阔", "温暖", "熟悉", "人少")],
    "cal_clothing": [f"经常穿{x}" for x in ("衬衫", "运动鞋", "卫衣", "牛仔裤", "毛衣", "外套")],
    "cal_drink": [f"平时爱喝{x}" for x in ("红茶", "绿茶", "咖啡", "牛奶", "豆浆", "柠檬水")],
    "cal_weekend": [f"周末常去{x}" for x in ("公园", "图书馆", "菜市场", "美术馆", "健身房", "书店")],
    "cal_contact": [f"经常和{x}聊天" for x in ("姐姐", "室友", "同学", "老朋友", "父母", "邻居")],
    "val_home": [f"住在{x}" for x in ("学校附近", "公司附近", "老城区", "郊区", "河边", "车站旁")],
    "val_weather": [f"很喜欢{x}天" for x in ("晴", "雨", "雪", "阴", "有风的", "凉爽的")],
    "val_learning": [f"每天花半小时{x}" for x in ("读书", "背单词", "练琴", "练字", "写日记", "做题")],
    "val_event": [f"昨天去了一趟{x}" for x in ("银行", "超市", "医院", "学校", "博物馆", "电影院")],
    "test_transport_change": [f"现在改成{x}去公司了" for x in ("骑车", "步行", "拼车", "开车", "骑电动车", "打车")],
    "test_craft": [f"最近在练{x}" for x in ("陶艺", "木工", "折纸", "刺绣", "书法", "攀岩")],
    "test_personality": [f"参加{x}后会很累" for x in ("大型聚会", "多人会议", "团建", "相亲", "聚餐", "社交活动")],
    "test_money": [f"每月会存下{x}元" for x in ("三百", "五百", "一千", "两千", "八百", "六百")],
    "test_pet_care": [f"每天给{x}喂食" for x in ("兔子", "仓鼠", "鹦鹉", "乌龟", "小狗", "金鱼")],
    "test_noise": [f"听到{x}会烦躁" for x in ("施工声", "鸣笛", "争吵", "敲门声", "喇叭声", "闹铃")],
    "test_work_style": [f"喜欢{x}处理事情" for x in ("先列清单再", "一个人", "和同伴一起", "早上", "安静地", "分成小步")],
    "test_night": [f"睡前习惯{x}" for x in ("拉伸", "洗热水澡", "听雨声", "整理房间", "看纸质书", "写两句话")],
}
FACT = [
    "我{c}。", "说到我自己，我{c}。", "我{c}，一直都是这样。", "我{c}，之前没怎么提过。",
    "我{c}，你觉得怎么调整比较好？", "我明明{c}，怎么还是这么累啊？",
    "我{c}，但真希望以后能换个节奏。", "我{c}，原因还没想清楚。",
    "不是别人，我{c}。", "我说自己{c}，是在讲真实情况。",
]
OTHER = [
    "我{c}吗？", "我是不是{c}", "你知道我是否{c}？", "假如我{c}，会怎么样？",
    "真希望我{c}。", "我朋友{c}。", "同事在讲他自己：我{c}。", "故事的主角说：我{c}。",
    "帮我写一句含有‘我{c}’的台词。", "请假装我{c}，继续这个虚构故事。",
]


def build(out):
    if out.exists():
        raise ValueError("refuse to overwrite frozen curriculum")
    out.mkdir(parents=True)
    partitions = {k: [] for k in ("train", "calibration", "validation", "test")}
    for family, cores in FAMILIES.items():
        split = next((s for prefix, s in (("cal_", "calibration"), ("val_", "validation"),
                                         ("test_", "test")) if family.startswith(prefix)), "train")
        for index, core in enumerate(cores):
            for label, templates in (("yes", FACT), ("no", OTHER)):
                for template_id, template in enumerate(templates):
                    partitions[split].append({"id": f"{family}-{index}-{label}-{template_id}",
                        "family": family, "source": template.format(c=core), "expected": label,
                        "provenance": "authored_synthetic", "template": f"{label}-{template_id}"})
    # Additional minimal pairs ensure punctuation and missing 我 are not sole cues.
    extras = [
        ("猫叫小米，养了三年了。", "yes"), ("家里养的狗叫花花。", "yes"),
        ("昨天只睡了四个小时，现在整个人没精神。", "yes"), ("最近压力大，晚上睡得浅。", "yes"),
        ("我怎么这么烦啊", "yes"), ("我为什么总是这么累呢？", "yes"),
        ("我今天怎么又这么难过？", "yes"), ("我明明已经做完工作了，怎么还是担心？", "yes"),
        ("难道我喜欢游泳吗", "no"), ("我已经做完了吗", "no"),
        ("想象一下，我已经完成了所有工作。", "no"), ("小说里那个人很疲惫，不是说我。", "no"),
    ]
    for i, (source, expected) in enumerate(extras):
        partitions["train"].append({"id": f"hard-{i}", "family": "train_hard", "source": source,
                                     "expected": expected, "provenance": "authored_synthetic"})
    manifest = {"scope": "Authored synthetic pilot; families disjoint, templates shared across partitions. Not human gold or independent natural-dialogue accuracy.",
                "seed": 42, "partitions": {}, "prompt": ASSERTION_ZH}
    for split, rows in partitions.items():
        random.Random(42).shuffle(rows)
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        (out / f"{split}.jsonl").write_text(text)
        manifest["partitions"][split] = {"rows": len(rows), "families": sorted({r["family"] for r in rows}),
                                          "sha256": hashlib.sha256(text.encode()).hexdigest()}
    (out / "prompt.json").write_text(json.dumps({"system": ASSERTION_ZH}, ensure_ascii=False, indent=2) + "\n")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.out)
    print(json.dumps({k: v["rows"] for k, v in result["partitions"].items()}))
