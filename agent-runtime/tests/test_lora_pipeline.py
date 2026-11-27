import json

import pytest

from training.lora.common import TEXT, metrics, render
from training.lora.step2_convert import ANCHOR, SkipRecord, convert, parse_calls
from training.lora.step3_samples import run as build_samples
from training.lora.step3_samples import samples, split_by_group
from training.lora.step4_train import fits_budget, lr_at


def record(messages, tools=None):
    tools = tools or [{"name": "Get Weather", "description": "Weather for a city.",
                       "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}]
    return {"system": "You are helpful. " + ANCHOR + "\n" + json.dumps(tools) + "\nEnd.",
            "conversations": messages}


WEATHER = [{"from": "user", "value": "Weather in Paris?"},
           {"from": "assistant", "value": "[Get Weather(city='Paris')]"},
           {"from": "tool", "value": '[{"temp": 21}]'},
           {"from": "assistant", "value": "It is 21°C in Paris."}]


# ---- step 2: parsing publisher call blocks ----

def test_names_with_spaces_apostrophes_and_nested_literals():
    calls = parse_calls(
        "[Get Coins' Data(filters={'codes':['X-2'], 'enabled':true, 'limit':null}), /lookup(key=\"a)b\")]",
        ["Get Coins' Data", "/lookup", "Get"])
    assert calls == [{"name": "Get Coins' Data", "arguments": {
                         "filters": {"codes": ["X-2"], "enabled": True, "limit": None}}},
                     {"name": "/lookup", "arguments": {"key": "a)b"}}]


@pytest.mark.parametrize("text", ["[Lookup(key=1+2)]", "[Unknown(key='A')]", "[Lookup(**{'key':'A'})]",
                                  "[Lookup(key='A',key='B')]", "[Lookup(key='A')]; extra",
                                  "[Lookup(key='A'),]", "[]"])
def test_invalid_call_blocks_are_rejected_not_guessed(text):
    with pytest.raises((SkipRecord, SyntaxError, ValueError)):
        parse_calls(text, ["Lookup"])


def test_call_text_is_never_executed(tmp_path):
    marker = tmp_path / "executed"
    with pytest.raises((SkipRecord, ValueError)):
        parse_calls(f"[Lookup(key=__import__('pathlib').Path({str(marker)!r}).touch())]", ["Lookup"])
    assert not marker.exists()


def test_convert_turns_call_blocks_into_tool_call_events():
    episode = convert(record(WEATHER))
    assert [e["role"] for e in episode["events"]] == ["user", "tool_call", "observation", "assistant"]
    assert episode["events"][1]["calls"] == [{"name": "Get Weather", "arguments": {"city": "Paris"}}]


def test_an_unparsable_call_drops_the_whole_record():
    broken = [WEATHER[0], {"from": "assistant", "value": "[Get Weather(city=)]"}]
    with pytest.raises(SkipRecord):
        convert(record(broken))  # never silently relabelled as plain assistant text


def test_virtual_think_tool_is_rejected():
    tools = [{"name": "think", "description": "", "parameters": {"type": "object"}}]
    with pytest.raises(SkipRecord):
        convert(record(WEATHER[:1], tools))


# ---- step 3: samples and splits ----

def test_each_action_becomes_one_sample_and_the_target_is_not_in_its_input():
    first, second = samples(convert(record(WEATHER)))
    assert first["targetIndices"] == [0] and first["observedAction"] == "tool_call"
    assert first["context"] == [{"role": "user", "content": "Weather in Paris?"}]
    assert "Paris')]" not in render(first, 0)  # the call being predicted is absent
    assert second["targetIndices"] == [len(second["candidateTools"]) - 1]
    assert second["candidateTools"][-1] == TEXT
    assert [e["role"] for e in second["context"]] == ["user", "tool_call", "observation"]


def test_parallel_calls_supervise_every_called_tool():
    tools = [{"name": n, "description": n, "parameters": {"type": "object"}} for n in ("A", "B", "C")]
    episode = convert(record([{"from": "user", "value": "do both"},
                              {"from": "assistant", "value": "[C(), A()]"}], tools))
    assert samples(episode)[0]["targetIndices"] == [0, 2]


def _episode(i, tool, query):
    tools = [{"name": tool, "description": tool, "parameters": {"type": "object"}}]
    return convert(record([{"from": "user", "value": query}, {"from": "assistant", "value": f"[{tool}()]"}], tools))


def test_shared_tool_or_first_query_keeps_episodes_in_one_split():
    episodes = [_episode(i, f"Tool{i % 7}", f"question {i}") for i in range(60)]
    episodes += [_episode(100, "Lonely", "question 3")]  # same first query as episode 3
    split = split_by_group(episodes)
    for a in episodes:
        for b in episodes:
            if a["tools"][0]["name"] == b["tools"][0]["name"]:
                assert split[a["id"]] == split[b["id"]]
    assert split[episodes[-1]["id"]] == split[episodes[3]["id"]]
    assert set(split.values()) == {"train", "validation", "test"}


def test_build_writes_three_splits_and_drops_duplicates(tmp_path):
    episodes = [_episode(i, f"Tool{i}", f"q{i}") for i in range(30)]
    path = tmp_path / "episodes.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in episodes + episodes[:5]))
    stats = build_samples(path, tmp_path / "samples")
    assert stats["episodes"] == 30 and sum(stats["samples"].values()) == 30
    assert {p.name for p in (tmp_path / "samples").glob("*.jsonl")} == {"train.jsonl", "validation.jsonl", "test.jsonl"}


# ---- step 4: budget, schedule and metrics ----

class FakeTokenizer:
    def __call__(self, texts, add_special_tokens=False):
        return {"input_ids": [list(range(len(t) // 10)) for t in texts]}


def test_overlong_samples_are_excluded_not_truncated():
    row = samples(convert(record(WEATHER)))[0]
    assert fits_budget(FakeTokenizer(), row, max_tokens=10_000, max_padded=100_000)
    assert not fits_budget(FakeTokenizer(), row, max_tokens=5, max_padded=100_000)


def test_learning_rate_warms_up_then_decays_to_a_floor():
    rates = [lr_at(s, 100, 1.0) for s in (1, 10, 50, 100)]
    assert rates[0] == pytest.approx(0.099) and rates[1] == pytest.approx(0.9)
    assert rates[2] == pytest.approx(0.5) and rates[3] == pytest.approx(0.1)


def test_metrics_split_agreement_by_action_type():
    rows = [{"scores": [2.0, 0.1], "targetIndices": [0], "observedAction": "tool_call"},
            {"scores": [2.0, 0.1], "targetIndices": [1], "observedAction": "assistant_text"},
            {"scores": [0.0, 1.0], "targetIndices": [1], "observedAction": "assistant_text"}]
    result = metrics(rows)
    assert result["all"]["top1"] == pytest.approx(2 / 3)
    assert result["toolCall"]["top1"] == 1.0 and result["assistantText"]["top1"] == 0.5
