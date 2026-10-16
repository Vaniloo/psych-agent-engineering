"""Append versioned boundary cases without moving old heldout rows into training."""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from psych_agent_runtime.memory_lifecycle_contract import CONTRACT, INSTRUCTIONS, normalize

from .memory_lifecycle_data import SPLITS, TASKS, digest


def boundary_scenarios(i):
    places = [
        "竹影社区",
        "南桥书屋",
        "海棠花园",
        "云杉营地",
        "金桂庭院",
        "青石村口",
        "星河书院",
        "清溪广场",
        "晨露小筑",
        "枫林工坊",
    ]
    activities = [
        "画水彩",
        "练陶笛",
        "做手工",
        "写随笔",
        "下围棋",
        "练书法",
        "读诗集",
        "编织",
        "拍花草",
        "做陶艺",
    ]
    relatives = ["堂姐", "表弟", "舅舅", "姑妈", "哥哥", "姨妈", "堂弟", "表姐", "叔叔", "妹妹"]
    objects = ["相册", "毛毯", "书签", "手套", "明信片", "笔记本", "帆布包", "茶杯", "帽子", "画册"]
    place, activity = places[i // 10], activities[i % 10]
    other = activities[(i + 1) % 10]
    person, obj = relatives[i % 10], objects[i // 10]
    old = f"我平时喜欢在{place}{activity}。"
    sent = f"我每周从{place}给{person}寄{obj}。"
    reverse = f"{person}每周从{place}给我寄{obj}。"
    appointment = f"我在{place}的固定预约是每周二。"
    corrected = f"更正：我在{place}的固定预约不是每周二，是每周四。"
    pet_old = f"我唯一一只猫叫{place[:2]}。"
    pet_new = f"我唯一一只猫叫{place[:2]}{activity[:1]}。"
    rows = []

    def add(
        name, source, candidate, existing, labels, scope="stable", old_scope="stable", role="user"
    ):
        rows.append((name, role, source, candidate, scope, existing, old_scope, labels))

    add(
        "paraphrase",
        f"在{place}{activity}是我平时喜欢的事。",
        f"在{place}{activity}是我平时喜欢的事。",
        old,
        (1, 1, 1, 0, 0),
    )
    add(
        "coexisting_preferences",
        f"我平时也喜欢在{place}{other}。",
        f"我平时也喜欢在{place}{other}。",
        old,
        (1, 1, 0, 0, 0),
    )
    add("different_attribute", f"我在{place}上班。", f"我在{place}上班。", old, (1, 1, 0, 0, 0))
    add(
        "unrelated_identity",
        f"我每周固定在{place}{activity}。",
        f"我每周固定在{place}{activity}。",
        pet_old,
        (1, 1, 0, 0, 0),
    )
    add(
        "temporary_exception",
        f"今天我不想在{place}{activity}。",
        f"今天我不想在{place}{activity}。",
        old,
        (0, 1, 0, 0, 0),
        "temporary",
    )
    add(
        "separate_dates",
        f"昨天我在{place}{activity}时很紧张。",
        f"昨天我在{place}{activity}时很紧张。",
        f"今天我在{place}{activity}时很放松。",
        (0, 1, 0, 0, 0),
        "temporary",
        "temporary",
    )
    add("third_person", f"我{person}说她平时喜欢在{place}{activity}。", old, old, (0, 0, 1, 0, 0))
    add(
        "quoted_first_person",
        f"我在小说里读到一句话：“我平时喜欢在{place}{activity}。”",
        old,
        old,
        (0, 0, 1, 0, 0),
    )
    add("hypothetical", f"假如我住在{place}，我可能会喜欢{activity}。", old, old, (0, 0, 1, 0, 0))
    add(
        "unconfirmed_plan",
        f"我打算以后每周去{place}{activity}，还没开始。",
        f"我每周固定去{place}{activity}。",
        old,
        (0, 0, 0, 0, 0),
    )
    add("reversed_roles", sent, reverse, sent, (1, 0, 0, 0, 0))
    add("reciprocal_relations", reverse, reverse, sent, (1, 1, 0, 0, 0))
    add(
        "explicit_schedule_correction",
        corrected,
        f"我在{place}的固定预约是每周四。",
        appointment,
        (1, 1, 0, 1, 1),
    )
    add(
        "explicit_preference_change",
        f"我已经不再喜欢在{place}{activity}，现在改为喜欢{other}。",
        f"我现在喜欢在{place}{other}，不再喜欢{activity}。",
        old,
        (1, 1, 0, 1, 1),
    )
    add("unresolved_same_attribute", pet_new, pet_new, pet_old, (1, 1, 0, 0, 1))
    add("retention_optout", "别长期保存这个偏好：" + old, old, old, (0, 1, 1, 0, 0))
    add(
        "remember_specific_event",
        f"帮我记住：我今天在{place}第一次学会了{activity}。",
        f"我今天在{place}第一次学会了{activity}。",
        old,
        (1, 1, 0, 0, 0),
        "temporary",
    )
    add(
        "assistant_suggestion",
        f"建议你以后在{place}{activity}。",
        old,
        old,
        (0, 0, 1, 0, 0),
        role="assistant",
    )
    return rows


def fresh_challenge():
    # Written before any v2 scoring. These are development expectations, not
    # independent human labels; domains/word order differ from training templates.
    return [
        (
            "coexist",
            "我一直喜欢攀岩，也喜欢做烘焙。",
            "我一直喜欢做烘焙。",
            "我一直喜欢攀岩。",
            (1, 1, 0, 0, 0),
        ),
        (
            "cross_attribute",
            "我的职业是配音演员。",
            "我的职业是配音演员。",
            "我偏爱安静的沟通方式。",
            (1, 1, 0, 0, 0),
        ),
        (
            "paraphrase",
            "比起打电话，我一贯更喜欢发文字消息。",
            "比起打电话，我一贯更喜欢发文字消息。",
            "我一贯更喜欢发文字消息而不是打电话。",
            (1, 1, 1, 0, 0),
        ),
        (
            "temporary",
            "就今晚我不想弹吉他。",
            "就今晚我不想弹吉他。",
            "我一直喜欢弹吉他。",
            (0, 1, 0, 0, 0),
            "temporary",
        ),
        (
            "explicit_change",
            "我已经换工作了，现在当园艺师，不做会计了。",
            "我现在的工作是园艺师。",
            "我现在的工作是会计。",
            (1, 1, 0, 1, 1),
        ),
        (
            "schedule",
            "纠正一下，咨询预约定在星期五，星期一是我上次记错了。",
            "我的咨询预约在星期五。",
            "我的咨询预约在星期一。",
            (1, 1, 0, 1, 1),
        ),
        (
            "unresolved_value",
            "我唯一的乌龟名字是小墨。",
            "我唯一的乌龟名字是小墨。",
            "我唯一的乌龟名字是青豆。",
            (1, 1, 0, 0, 1),
        ),
        (
            "other_person",
            "我的搭档不吃香菜，我说的是她的口味。",
            "我不吃香菜。",
            "我不吃香菜。",
            (0, 0, 1, 0, 0),
        ),
        (
            "quotation",
            "我只是念出了剧本里那句‘我恐高’，这不是在介绍自己。",
            "我恐高。",
            "我恐高。",
            (0, 0, 1, 0, 0),
        ),
        (
            "uncertain",
            "我不确定自己会不会对坚果过敏。",
            "我对坚果过敏。",
            "我对坚果过敏。",
            (0, 0, 1, 0, 0),
        ),
        (
            "reversed",
            "我每月给外婆汇生活费。",
            "外婆每月给我汇生活费。",
            "我每月给外婆汇生活费。",
            (1, 0, 0, 0, 0),
        ),
        (
            "reciprocal",
            "室友经常给我寄书。",
            "室友经常给我寄书。",
            "我经常给室友寄书。",
            (1, 1, 0, 0, 0),
        ),
        (
            "quantity",
            "我的晨跑习惯是每次二十分钟。",
            "我每次晨跑两小时。",
            "我每次晨跑二十分钟。",
            (1, 0, 0, 0, 1),
        ),
        (
            "plan",
            "等搬完家，我想试着每周练普拉提，现在还没有。",
            "我现在每周练普拉提。",
            "我现在每周练普拉提。",
            (0, 0, 1, 0, 0),
        ),
        (
            "one_off",
            "今天碰到旧同学，我很开心。",
            "我一直都很乐观。",
            "我一直都很乐观。",
            (0, 0, 1, 0, 0),
        ),
        (
            "optout",
            "这只是随口一说，不要存成长期记忆：我爱吃甜点。",
            "我爱吃甜点。",
            "我爱吃甜点。",
            (0, 1, 1, 0, 0),
        ),
        (
            "explicit_event",
            "请替我记住，我昨天拿到了驾照。",
            "我昨天拿到了驾照。",
            "我上周还在准备驾考。",
            (1, 1, 0, 0, 0),
            "temporary",
            "temporary",
        ),
        (
            "different_days",
            "我周六在演出前很紧张。",
            "我周六在演出前很紧张。",
            "我周日在演出后很平静。",
            (0, 1, 0, 0, 0),
            "temporary",
            "temporary",
        ),
        (
            "new_detail",
            "我喜欢意大利面，尤其是番茄口味。",
            "我喜欢意大利面，尤其是番茄口味。",
            "我喜欢意大利面。",
            (1, 1, 0, 0, 0),
        ),
        (
            "negation",
            "我不会开手动挡汽车。",
            "我不会开手动挡汽车。",
            "我会开手动挡汽车。",
            (1, 1, 0, 0, 1),
        ),
        (
            "english_separate",
            "I work as a librarian.",
            "I work as a librarian.",
            "I enjoy playing chess.",
            (1, 1, 0, 0, 0),
        ),
        (
            "english_temporary",
            "Only today I don't want to swim.",
            "Only today I don't want to swim.",
            "I usually enjoy swimming.",
            (0, 1, 0, 0, 0),
            "temporary",
        ),
        (
            "english_subject",
            "My cousin is afraid of heights, not me.",
            "I am afraid of heights.",
            "I am afraid of heights.",
            (0, 0, 1, 0, 0),
        ),
        (
            "english_correction",
            "Correction: my appointment is on Thursday, not Tuesday.",
            "My appointment is on Thursday.",
            "My appointment is on Tuesday.",
            (1, 1, 0, 1, 1),
        ),
    ]


def state(source, candidate, scope, existing, old_scope, role="user"):
    return {
        "source": {"role": role, "content": source},
        "candidate": {"content": candidate, "scope": scope},
        "existing": {"content": existing, "scope": old_scope},
    }


def build(parent, out):
    if out.exists():
        raise ValueError("refuse to overwrite refined curriculum")
    meta = json.loads((parent / "manifest.json").read_text())
    if meta["contractVersion"] != CONTRACT or meta["instructionsSha256"] != digest(INSTRUCTIONS):
        raise ValueError("parent contract changed")
    raw_parts = {s: (parent / (s + ".jsonl")).read_bytes() for s in SPLITS}
    if any(hashlib.sha256(raw).hexdigest() != meta["hashes"][s] for s, raw in raw_parts.items()):
        raise ValueError("parent split checksum changed")
    parts = {
        s: [json.loads(line) for line in raw.decode().splitlines()] for s, raw in raw_parts.items()
    }
    challenge_raw = (parent / "challenge/cases.jsonl").read_bytes()
    if (
        hashlib.sha256(challenge_raw).hexdigest()
        != json.loads((parent / "challenge/manifest.json").read_text())["casesSha256"]
    ):
        raise ValueError("parent challenge checksum changed")
    challenge = [json.loads(line) for line in challenge_raw.decode().splitlines()]
    for i, row in enumerate(fresh_challenge()):
        name, source, candidate, existing, labels, *scopes = row
        new_state = state(
            source,
            candidate,
            scopes[0] if scopes else "stable",
            existing,
            scopes[1] if len(scopes) > 1 else "stable",
        )
        for task, label in zip(TASKS, labels, strict=True):
            challenge.append(
                {
                    "id": f"lifecycle-edge-heldout-{i}-{task}",
                    "family": f"lifecycle-edge-heldout-{i}",
                    "group": name,
                    "state": normalize(task, new_state),
                    "expected": {task: "yes" if label else "no"},
                }
            )
    forbidden = {
        digest({"task": t, "state": normalize(t, r["state"])})
        for r in challenge
        for t in r["expected"]
    }
    seen = {}
    for split, rows in parts.items():
        for r in rows:
            key = digest({"task": r["task"], "state": normalize(r["task"], r["state"])})
            if key in forbidden or key in seen:
                raise ValueError("parent contains overlapping or challenge input")
            seen[key] = r["label"]
    rng = random.Random(1610)
    groups = list(range(100))
    rng.shuffle(groups)
    mapping = {
        i: ("train" if rank < 40 else SPLITS[1 + (rank - 40) // 20])
        for rank, i in enumerate(groups)
    }
    added = {s: [] for s in SPLITS}
    excluded = Counter()
    for split in reversed(SPLITS):
        for i in range(100):
            if mapping[i] != split:
                continue
            for (
                name,
                role,
                source,
                candidate,
                scope,
                existing,
                old_scope,
                labels,
            ) in boundary_scenarios(i):
                new_state = state(source, candidate, scope, existing, old_scope, role)
                for task, label in zip(TASKS, labels, strict=True):
                    value = "yes" if label else "no"
                    clean = normalize(task, new_state)
                    key = digest({"task": task, "state": clean})
                    if key in forbidden:
                        excluded["challenge_overlap"] += 1
                        continue
                    if key in seen:
                        if seen[key] != value:
                            raise ValueError("conflicting refinement labels")
                        excluded["duplicate_input"] += 1
                        continue
                    seen[key] = value
                    added[split].append(
                        {
                            "task": task,
                            "state": clean,
                            "label": value,
                            "family": f"lifecycle-edge-family-{i}",
                            "source": "authored_boundary_templates",
                            "scenario": name,
                            "annotationStatus": "programmatic",
                            "curriculum": "lifecycle_edges",
                        }
                    )
    families = {s: {r["family"] for r in parts[s] + added[s]} for s in SPLITS}
    if any(families[a] & families[b] for a in SPLITS for b in SPLITS if a != b):
        raise ValueError("cross-split family leakage")
    out.mkdir(parents=True)
    hashes, counts = {}, {}
    for split in SPLITS:
        rng.shuffle(added[split])
        rows = parts[split] + added[split]
        raw = (
            raw_parts[split]
            + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in added[split]).encode()
        )
        (out / (split + ".jsonl")).write_bytes(raw)
        hashes[split] = hashlib.sha256(raw).hexdigest()
        counts[split] = {
            "rows": len(rows),
            "parentRowsKept": len(parts[split]),
            "addedRows": len(added[split]),
            "families": len(families[split]),
            "tasks": dict(Counter(r["task"] + ":" + r["label"] for r in rows)),
        }
    (out / "challenge").mkdir()
    raw = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in challenge).encode()
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
        "parentManifestSha256": hashlib.sha256((parent / "manifest.json").read_bytes()).hexdigest(),
        "parentChallengeSha256": hashlib.sha256(challenge_raw).hexdigest(),
        "scope": "Boundary templates informed by v1 errors; fact families disjoint, shared templates remain. Fresh authored challenge is development regression, not independent human gold.",
        "teacherApiUsed": False,
        "deploymentEnabled": False,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.parent, args.out), ensure_ascii=False))
