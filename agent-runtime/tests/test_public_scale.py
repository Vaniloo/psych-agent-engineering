import copy

import pytest

from psych_agent_runtime.decision_contract import MEMORY_QUESTIONS, ROUTER_QUESTIONS
from training.build_public_scale import memory_rows, unique
from training.scale_executor import scenarios
from training.scale_public import digest, source_cases, validate_labels


class Tokenizer:
    def encode(self, text, **kwargs):
        return list(text)


def training_conversation():
    for i in range(100):
        messages = [
            {
                "role": "user",
                "content": f"我今天整理了第{i}本笔记，现在觉得比刚才轻松了一些。",
                "ip": "private",
            },
            {"role": "assistant", "content": "这条后续回复绝不能进入当前输入。"},
        ]
        canonical = [{"role": m["role"], "content": m["content"]} for m in messages]
        if int(digest(canonical)[:8], 16) % 100 < 70:
            return messages
    raise AssertionError("fixture missing")


def test_source_omits_metadata_future_and_never_truncates():
    messages = training_conversation()
    rows = source_cases(messages, "fixture", Tokenizer(), max_tokens=1000)
    assert len(rows) == 1
    assert rows[0]["state"]["history"] == []
    assert "ip" not in str(rows)
    assert "后续回复" not in str(rows)
    assert not source_cases(messages, "fixture", Tokenizer(), max_tokens=5)


def test_entire_conversation_is_reserved_and_detected_secrets_are_skipped():
    for i in range(100):
        messages = [{"role": "user", "content": f"我今天第{i}次想聊自己的学习感受，希望能被理解。"}]
        if int(digest(messages)[:8], 16) % 100 >= 70:
            assert source_cases(messages, "fixture", Tokenizer(), max_tokens=1000) == []
            break
    messages = training_conversation()
    messages[-1]["content"] = "someone@example.com"
    assert source_cases(messages, "fixture", Tokenizer(), max_tokens=1000) == []


def label_fixture():
    row = {"caseId": "one", "state": {"message": "我昨天读了一页书，今天还没有继续。"}}
    label = {
        "caseId": "one",
        "router": {k: next(iter(v["criteria"])) for k, v in ROUTER_QUESTIONS.items()},
        "memory": {k: "no" for k in ["episodic", "preference", "procedural", "semantic"]},
        "memoryQuery": "我昨天到底做了什么呢？",
        "factSpan": "我昨天完成了三页书阅读",
    }
    assert all("no" in MEMORY_QUESTIONS[k]["criteria"] for k in label["memory"])
    return row, label


def test_teacher_ids_and_semantic_consistency_are_checked_and_invented_fact_dropped():
    row, label = label_fixture()
    result = validate_labels([row], {"labels": [label]})["one"]
    assert result["memoryQuery"] == result["factSpan"] == ""
    label["memory"]["episodic"] = "yes"
    with pytest.raises(ValueError, match="contradict"):
        validate_labels([row], {"labels": [label]})
    label["memory"]["semantic"] = "yes"
    label["caseId"] = "wrong"
    with pytest.raises(ValueError, match="identifiers"):
        validate_labels([row], {"labels": [label]})


def test_dedup_excludes_holdout_and_oversized_state_without_padding():
    reserved = {"state": {"x": "reserved"}}
    valid = {"state": {"x": "valid"}}
    oversized = {"state": {"x": "a" * 300}}
    rows, skips = unique(
        [reserved, oversized, valid, copy.deepcopy(valid)],
        {digest(reserved["state"])},
        Tokenizer(),
        5,
    )
    assert rows == [valid]
    assert skips == {"duplicateOrHeldOut": 2, "tokenBudget": 1}


def test_source_derived_memory_remains_draft_with_separate_ownership_labels():
    item = {
        "state": {"message": "我昨天读了一页书，今天还没有继续。"},
        "memory": {"episodic": "yes", "preference": "no", "procedural": "no", "semantic": "yes"},
        "memoryQuery": "我昨天读了多少页书？",
        "factSpan": "我昨天读了一页书",
        "conversationId": "g",
        "source": "fixture",
        "caseId": "one",
        "teacher": "fixture",
    }
    rows = memory_rows([item])
    assert all(r["reviewed"] is False and r["annotationStatus"] == "teacher_draft" for r in rows)
    suff = [r for r in rows if "evidence_sufficient" in r["expected"]]
    assert {r["expected"]["evidence_sufficient"] for r in suff} == {"yes", "no"}
    assert all(
        r["expected"]["evidence_sufficient"] == "no"
        for r in suff
        if r["state"]["evidence"][0]["role"] == "assistant"
    )


def test_generated_goals_must_be_distinct_and_safe():
    task = {"goalTitle": "开始整理日常笔记", "actionTitle": "服药来缓解紧张感", "expressions": {}}
    with pytest.raises(ValueError, match="distinct"):
        scenarios({"tasks": [task, task]}, {}, 1, Tokenizer())
    second = {**task, "goalTitle": "开始安排学习计划"}
    with pytest.raises(ValueError, match="unsafe"):
        scenarios({"tasks": [task, second]}, {}, 1, Tokenizer())
