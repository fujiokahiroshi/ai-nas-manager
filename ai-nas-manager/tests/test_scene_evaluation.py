from scene_evaluation import match_boundaries, run_adaptive_memory, tune_adaptive_memory
from scene_segmentation import AdaptiveMemoryConfig


def trace() -> list[dict[str, object]]:
    return [
        {"timestamp_ms": timestamp, "vector": [1.0, 0.0]}
        for timestamp in (0, 500, 1_000, 1_500)
    ] + [
        {"timestamp_ms": timestamp, "vector": [0.0, 1.0]}
        for timestamp in (2_000, 2_500, 3_000, 3_500)
    ]


def test_boundary_matching_reports_error_and_misses() -> None:
    result = match_boundaries([2_100, 8_000], [2_000, 5_000], tolerance_ms=500)
    assert result["true_positive"] == 1
    assert result["false_positive"] == 1
    assert result["false_negative"] == 1
    assert result["matches"][0]["error_ms"] == 100


def test_run_adaptive_memory_is_replayable() -> None:
    events = run_adaptive_memory(trace(), AdaptiveMemoryConfig(
        high_threshold=0.2,
        low_threshold=0.1,
        confirmation_samples=2,
        min_scene_ms=1_000,
        cooldown_ms=0,
    ))
    assert [event["boundary_ms"] for event in events] == [2_000]


def test_tuner_finds_a_matching_shadow_configuration() -> None:
    result = tune_adaptive_memory(trace(), [2_000], tolerance_ms=500)
    assert result["evaluated_configurations"] > 100
    assert result["config"]["confirmation_samples"] >= 2
    assert result["metrics"]["f1"] == 1.0
    assert result["boundaries"][0]["boundary_ms"] == 2_000
