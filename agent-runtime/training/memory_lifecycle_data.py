"""Frozen synthetic lifecycle curriculum plus untouched legacy-task replay splits."""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from psych_agent_runtime.memory_lifecycle_contract import CONTRACT, INSTRUCTIONS, normalize

TASKS = ("write_worthy", "grounded", "duplicate", "supersedes", "conflict")
SPLITS = ("train", "calibration", "validation", "test")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def scenarios(i):
    places = [
        "河畔书店",
        "社区图书馆",
        "湖边公园",
        "校园草坪",
        "街角茶室",
        "家中阳台",
        "小区花园",
        "林间步道",
        "海边长椅",
        "旧城书屋",
    ]
    sounds = [
        "轻音乐",
        "钢琴曲",
        "古琴曲",
        "爵士乐",
        "民谣",
        "小提琴曲",
        "笛子曲",
        "古典乐",
        "吉他曲",
        "自然白噪音",
        "合唱曲",
        "口琴曲",
    ]
    place, sound = places[i // 12], sounds[i % 12]
    other = sounds[(i + 1) % 12]
    old = f"我平时喜欢在{place}听{sound}放松。"
    pet = ["小", "阿", "白", "青", "糯", "暖", "圆", "软", "晴", "星", "月", "橙"][i % 12] + [
        "团",
        "豆",
        "米",
        "芝",
        "栗",
        "棉",
        "果",
        "桃",
        "柚",
        "糖",
    ][i // 12]
    old_pet, new_pet = f"我唯一一只兔子叫{pet}。", f"我唯一一只兔子叫{pet}仔。"
    temp = f"今天我不想在{place}听{sound}。"
    corrected = f"更正：我平时喜欢在{place}听的是{other}，不是{sound}。"
    today = f"今天我在{place}听{sound}时很高兴。"
    # Labels refer to the task definitions, not a clinical or independent judge.
    return [
        ("repeat", "user", old, old, "stable", old, "stable", (1, 1, 1, 0, 0)),
        ("question", "user", old[:-1] + "吗？", old, "stable", old, "stable", (0, 0, 1, 0, 0)),
        (
            "assistant",
            "assistant",
            f"建议你去{place}听{sound}。",
            old,
            "stable",
            old,
            "stable",
            (0, 0, 1, 0, 0),
        ),
        (
            "other_person",
            "user",
            old.replace("我平时", "我朋友平时"),
            old,
            "stable",
            old,
            "stable",
            (0, 0, 1, 0, 0),
        ),
        ("temporary", "user", temp, temp, "temporary", old, "stable", (0, 1, 0, 0, 0)),
        ("correction", "user", corrected, corrected, "stable", old, "stable", (1, 1, 0, 1, 1)),
        (
            "unresolved_conflict",
            "user",
            new_pet,
            new_pet,
            "stable",
            old_pet,
            "stable",
            (1, 1, 0, 0, 1),
        ),
        (
            "different_day",
            "user",
            today,
            today,
            "temporary",
            today.replace("今天", "昨天").replace("高兴", "低落"),
            "temporary",
            (0, 1, 0, 0, 0),
        ),
        (
            "remember_event",
            "user",
            "请记住：" + today,
            today,
            "temporary",
            old,
            "stable",
            (1, 1, 0, 0, 0),
        ),
        (
            "do_not_retain",
            "user",
            "不要长期记住这件事：" + old,
            old,
            "stable",
            old,
            "stable",
            (0, 1, 1, 0, 0),
        ),
    ]


def build(replay, old_challenge, out):
    if out.exists():
        raise ValueError("refuse to overwrite frozen lifecycle course")
    replay_manifest = json.loads((replay / "manifest.json").read_text())
    parts = {}
    for split in SPLITS:
        raw = (replay / (split + ".jsonl")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != replay_manifest["hashes"][split]:
            raise ValueError("legacy replay hash mismatch")
        parts[split] = [
            {**json.loads(line), "curriculum": "legacy_replay"}
            for line in raw.decode().splitlines()
        ]
    challenge = [json.loads(line) for line in old_challenge.read_text().splitlines()]
    rng = random.Random(1510)
    groups = list(range(120))
    rng.shuffle(groups)
    mapping = {
        i: ("train" if rank < 60 else SPLITS[1 + (rank - 60) // 20])
        for rank, i in enumerate(groups)
    }
    for i in range(120):
        split, family = mapping[i], f"lifecycle-fact-family-{i}"
        for name, role, source, candidate, scope, existing, old_scope, labels in scenarios(i):
            state = {
                "source": {"role": role, "content": source},
                "candidate": {"content": candidate, "scope": scope},
                "existing": {"content": existing, "scope": old_scope},
            }
            for task, label in zip(TASKS, labels, strict=True):
                parts[split].append(
                    {
                        "task": task,
                        "state": normalize(task, state),
                        "label": "yes" if label else "no",
                        "family": family,
                        "source": "authored_lifecycle_templates",
                        "scenario": name,
                        "annotationStatus": "programmatic",
                        "curriculum": "lifecycle",
                    }
                )
    # Different situations and entities; still authored development challenges.
    holdouts = [
        ("我喜欢游泳。", "我喜欢游泳。", "stable", "我喜欢游泳。", "stable", (1, 1, 1, 0, 0)),
        (
            "我以前爱喝咖啡，现在彻底改喝茶了。",
            "我现在改喝茶了。",
            "stable",
            "我平时喝咖啡。",
            "stable",
            (1, 1, 0, 1, 1),
        ),
        (
            "今天不想喝咖啡。",
            "今天不想喝咖啡。",
            "temporary",
            "我喜欢喝咖啡。",
            "stable",
            (0, 1, 0, 0, 0),
        ),
        (
            "我唯一的猫叫松露。",
            "我唯一的猫叫松露。",
            "stable",
            "我唯一的猫叫布丁。",
            "stable",
            (1, 1, 0, 0, 1),
        ),
        ("她说她特别怕狗。", "我怕狗。", "stable", "我怕狗。", "stable", (0, 0, 1, 0, 0)),
        (
            "请记住我今天通过了资格考试。",
            "我今天通过了资格考试。",
            "temporary",
            "我上周准备考试。",
            "temporary",
            (1, 1, 0, 0, 0),
        ),
        (
            "我今天有点难过。",
            "我一直很悲观。",
            "stable",
            "我昨天很开心。",
            "temporary",
            (0, 0, 0, 0, 0),
        ),
        (
            "我打算明年搬去杭州。",
            "我现在住在杭州。",
            "stable",
            "我目前住在上海。",
            "stable",
            (0, 0, 0, 0, 1),
        ),
        (
            "更正一下，上回说周二看诊记错了，其实是周三。",
            "看诊时间是周三。",
            "stable",
            "看诊时间是周二。",
            "stable",
            (1, 1, 0, 1, 1),
        ),
        (
            "别长期记住：我喜欢辣食。",
            "我喜欢辣食。",
            "stable",
            "我喜欢辣食。",
            "stable",
            (0, 1, 1, 0, 0),
        ),
        (
            "我给妹妹买了蓝色围巾。",
            "妹妹给我买了蓝色围巾。",
            "temporary",
            "我给妹妹买了蓝色围巾。",
            "temporary",
            (0, 0, 0, 0, 0),
        ),
        (
            "我这周开始每天走路去上班。",
            "我每天走路去上班。",
            "stable",
            "我喜欢散步。",
            "stable",
            (1, 1, 0, 0, 0),
        ),
    ]
    for i, (source, candidate, scope, existing, old_scope, labels) in enumerate(holdouts):
        state = {
            "source": {"role": "user", "content": source},
            "candidate": {"content": candidate, "scope": scope},
            "existing": {"content": existing, "scope": old_scope},
        }
        for task, label in zip(TASKS, labels, strict=True):
            challenge.append(
                {
                    "id": f"lifecycle-heldout-{i}-{task}",
                    "family": f"lifecycle-heldout-{i}",
                    "state": normalize(task, state),
                    "expected": {task: "yes" if label else "no"},
                }
            )
    forbidden = {
        digest({"task": task, "state": normalize(task, r["state"])})
        for r in challenge
        for task in r["expected"]
    }
    seen, excluded = {}, Counter()
    for split in reversed(SPLITS):
        cleaned = []
        for row in parts[split]:
            key = digest({"task": row["task"], "state": normalize(row["task"], row["state"])})
            if key in forbidden or key in seen:
                excluded["challenge_or_duplicate_input"] += 1
                continue
            seen[key] = split
            cleaned.append(row)
        parts[split] = cleaned
    families = {s: {r["family"] for r in rows} for s, rows in parts.items()}
    if any(families[a] & families[b] for a in SPLITS for b in SPLITS if a != b):
        raise ValueError("cross-split family leakage")
    out.mkdir(parents=True)
    hashes, counts = {}, {}
    for split, rows in parts.items():
        rng.shuffle(rows)
        raw = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()
        (out / (split + ".jsonl")).write_bytes(raw)
        hashes[split] = hashlib.sha256(raw).hexdigest()
        counts[split] = {
            "rows": len(rows),
            "families": len(families[split]),
            "tasks": dict(Counter(r["task"] + ":" + r["label"] for r in rows)),
        }
    raw = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in challenge).encode()
    (out / "challenge").mkdir()
    (out / "challenge/cases.jsonl").write_bytes(raw)
    (out / "challenge/manifest.json").write_text(
        json.dumps({"casesSha256": hashlib.sha256(raw).hexdigest()})
    )
    manifest = {
        "contractVersion": CONTRACT,
        "instructionsSha256": digest(INSTRUCTIONS),
        "hashes": hashes,
        "counts": counts,
        "exclusions": dict(excluded),
        "legacyManifestSha256": hashlib.sha256((replay / "manifest.json").read_bytes()).hexdigest(),
        "legacyChallengeSha256": hashlib.sha256(old_challenge.read_bytes()).hexdigest(),
        "scope": "Programmatic synthetic templates grouped by fact family; shared semantic templates remain. Heldout authored cases are not independent human gold.",
        "teacherApiUsed": False,
        "deploymentEnabled": False,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("replay", "old-challenge", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.replay, args.old_challenge, args.out), ensure_ascii=False))
