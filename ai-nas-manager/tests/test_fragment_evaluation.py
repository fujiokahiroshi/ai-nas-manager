from __future__ import annotations

from fragment_evaluation import (
    TimeInterval,
    aggregate,
    evaluate_item,
    match_points_to_intervals,
    prediction_points,
)
from online_fragmentation import fragment_config


def test_one_to_one_interval_matching() -> None:
    expected = [TimeInterval(2_000, 2_100), TimeInterval(5_000, 5_000)]
    assert match_points_to_intervals([1_900, 2_050, 5_800, 9_000], expected, tolerance_ms=1_000) == (2, 2, 0)


def test_prediction_points_omit_housekeeping_events() -> None:
    events = [
        {"kind": "open", "start_ms": 0, "observed_ms": 0, "reason": "stream_start"},
        {"kind": "update", "start_ms": 0, "observed_ms": 2_000, "reason": "audio_change"},
        {"kind": "open", "start_ms": 5_000, "observed_ms": 5_000, "reason": "continued_stream"},
        {"kind": "update", "start_ms": 5_000, "observed_ms": 7_000, "reason": "adaptive_change"},
    ]
    assert prediction_points(events) == ([5_000], [2_000, 7_000])


def test_evaluate_and_aggregate() -> None:
    item = {
        "id": "sample",
        "dataset": "custom",
        "video": "sample.mp4",
        "duration_ms": 10_000,
        "tasks": ["boundary", "importance"],
        "boundaries_ms": [[4_900, 5_100]],
        "importance": [{"start_ms": 1_500, "end_ms": 2_500, "score": 5}],
    }
    result = {
        "source": "sample.mp4",
        "events": [
            {"kind": "open", "start_ms": 0, "observed_ms": 0, "reason": "stream_start"},
            {"kind": "update", "start_ms": 0, "observed_ms": 2_000, "reason": "audio_change"},
            {"kind": "open", "start_ms": 5_000, "observed_ms": 5_000, "reason": "hard_cut"},
        ],
    }
    measured = evaluate_item(item, result, importance_threshold=4.0)
    assert measured["boundary"]["f1"] == 1.0
    assert measured["importance"]["recall"] == 1.0
    combined = aggregate([measured])
    assert combined["boundary"]["f1"] == 1.0


def test_all_importance_intervals_can_be_selected() -> None:
    item = {
        "id": "summe:sample",
        "dataset": "SumMe",
        "video": "sample.mp4",
        "duration_ms": 10_000,
        "tasks": ["importance"],
        "importance_selection": "all",
        "importance": [
            {"start_ms": 1_000, "end_ms": 2_000, "score": 1},
            {"start_ms": 7_000, "end_ms": 8_000, "score": 1},
        ],
    }
    result = {
        "source": "sample.mp4",
        "events": [
            {"kind": "update", "observed_ms": 1_500, "reason": "audio_change"},
        ],
    }
    measured = evaluate_item(item, result, importance_fraction=0.15)
    assert measured["importance"]["true_positive"] == 1
    assert measured["importance"]["false_negative"] == 1


def test_balanced_profile_enables_fusion() -> None:
    balanced = fragment_config("fused-v2-balanced")
    conservative = fragment_config("fused-v2-conservative")
    assert balanced.fusion_enabled is True
    assert balanced.visual_strong_threshold < conservative.visual_strong_threshold
