from fragment_admission import confirmed_records, evaluate_admission


def test_manual_marker_is_always_confirmed() -> None:
    decision = evaluate_admission({"manual_marker": True, "confidence": 0})
    assert decision.confirmed is True


def test_high_confidence_structured_observation_is_confirmed() -> None:
    decision = evaluate_admission({
        "observation_ja": "人物がボールを打っている",
        "action_ja": "スイングする",
        "objects": ["人物", "ボール"],
        "confidence": 0.86,
    })
    assert decision.state == "confirmed"


def test_low_confidence_candidate_is_not_confirmed() -> None:
    record = {"observation": "何かが見える", "objects": [], "confidence": 0.25}
    decision = evaluate_admission(record)
    record["admission_state"] = decision.state
    assert decision.state == "candidate"
    assert confirmed_records([record]) == []
