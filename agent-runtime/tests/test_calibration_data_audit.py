from training.audit_calibration_data import audit, digest, without_record_stamp
from training.clean_calibration_cohort import clean


def row(group, text, label="low"):
    return {
        "splitGroup": group,
        "source": "project_domain_curriculum_v1",
        "state": {"message": text, "history": [], "session_summary": ""},
        "expected": {"risk_band": label},
    }


def test_artificial_record_stamp_cannot_create_input_independence():
    a = row("cal", "我现在安全。（本次记录于11月1日7时34分。）")
    b = row("val", "我现在安全。（本次记录于11月2日8时55分。）")
    report = audit({"calibration": [a], "validation": [b]}, "router")
    assert not report["sameContentAcrossSplits"]
    assert len(report["recordStampOnlySeparationAcrossSplits"]) == 1
    assert a["state"]["message"].endswith("7时34分。）")


def test_real_action_time_and_denial_are_never_stripped():
    a = row("a", "我刚吞了过量药，十分钟前。")
    b = row("b", "我没有吞过量药，十分钟前。")
    c = row("c", "几年前我曾吞过量药，现在已恢复。")
    assert len({digest(without_record_stamp(r)) for r in [a, b, c]}) == 3


def test_clean_preserves_test_priority_labels_and_original_event_groups():
    training = [row("train", "训练输入")]
    a = row("event", "前缀一。有效输入")
    b = row("event", "前缀二。有效输入")
    parts = {
        "calibration": [a, b, row("bad", "训练输入")],
        "validation": [row("overlap", "测试输入"), row("val", "验证输入")],
        "test": [row("test", "测试输入")],
    }
    chosen, excluded = clean(parts, training, "router")
    assert len(chosen["validation"]) == 1
    assert len(chosen["calibration"]) == 2
    assert len(excluded) == 2
    assert (
        chosen["calibration"][0]["calibrationGroup"] == chosen["calibration"][1]["calibrationGroup"]
    )
    assert chosen["calibration"][0]["expected"] == a["expected"]
