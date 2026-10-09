import json

from training.prepare_dialogue_decisions import prepare


def test_turn_extraction_excludes_future_answers_and_keeps_conversations_in_one_split(tmp_path):
    source = tmp_path / "public.json"
    source.write_text(
        json.dumps(
            [
                {
                    "messages": [
                        {"role": "system", "content": "external persona"},
                        {"role": "user", "content": "我有些紧张"},
                        {"role": "assistant", "content": "过去的回复"},
                        {"role": "user", "content": "帮我安排第一步"},
                        {"role": "assistant", "content": "未来的答案不能泄漏"},
                    ]
                },
            ]
        )
    )
    cases, manifest = prepare([source, source])
    assert len(cases) == 2  # identical conversations across sources deduplicated
    assert len({item["split"] for item in cases}) == 1
    assert len({item["conversationId"] for item in cases}) == 1
    for case in cases:
        assert "未来的答案不能泄漏" not in json.dumps(case, ensure_ascii=False)
        assert "external persona" not in json.dumps(case, ensure_ascii=False)
        assert not case["reviewed"]
        assert all(label is None for label in case["expected"].values())
    assert manifest["labels"] == "pending_annotation_not_training_ready"
