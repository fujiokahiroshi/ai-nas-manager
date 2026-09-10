from __future__ import annotations

import pytest

from scene_segmentation import (
    BayesianOnlineChangeDetector,
    OnlineHybridSceneSegmenter,
    OnlineSceneConfig,
    SceneSample,
    cosine_similarity,
    pelt_boundaries,
)


def sample(timestamp_ms: int, change: float, similarity: float | None = None) -> SceneSample:
    return SceneSample(
        timestamp_ms,
        visual_change=change,
        object_change=change,
        audio_change=change,
        semantic_change=change,
        semantic_similarity=similarity,
    )


def test_online_detector_emits_delayed_boundary_while_stream_is_alive() -> None:
    detector = OnlineHybridSceneSegmenter(
        "camera",
        OnlineSceneConfig(
            cusum_threshold=0.25,
            bocpd_threshold=1.0,
            confirmation_ms=1_000,
            min_scene_ms=2_000,
            cooldown_ms=0,
        ),
    )
    events = []
    for timestamp in (0, 500, 1_000, 1_500, 2_000):
        events += detector.process(sample(timestamp, 0.01))
    events += detector.process(sample(2_500, 0.95))
    assert events == []
    events += detector.process(sample(3_000, 0.02))
    events += detector.process(sample(3_500, 0.02))
    assert len(events) == 1
    assert events[0].boundary_ms == 2_500
    assert events[0].emitted_ms == 3_500
    assert "cusum" in events[0].reasons


def test_semantic_continuity_vetoes_non_hard_candidate() -> None:
    detector = OnlineHybridSceneSegmenter(
        "camera",
        OnlineSceneConfig(
            cusum_threshold=0.20,
            bocpd_threshold=1.0,
            confirmation_ms=500,
            min_scene_ms=1_000,
            cooldown_ms=0,
            hard_change_threshold=0.95,
            semantic_join_threshold=0.80,
        ),
    )
    detector.process(sample(0, 0.0))
    detector.process(sample(500, 0.0))
    detector.process(sample(1_000, 0.50, similarity=0.95))
    assert detector.process(sample(1_500, 0.0, similarity=0.95)) == []


def test_bocpd_reset_probability_increases_for_new_regime() -> None:
    detector = BayesianOnlineChangeDetector(hazard=0.03, observation_variance=0.005)
    stable = [detector.update(0.03) for _ in range(20)]
    changed = detector.update(0.90)
    assert changed > max(stable[-5:])
    assert changed > 0.5


def test_pelt_finds_two_clear_multivariate_changes() -> None:
    values = [(0.0, 0.0)] * 10 + [(1.0, 0.9)] * 12 + [(0.2, 0.1)] * 10
    assert pelt_boundaries(values, penalty=0.8, min_size=3) == [10, 22]


def test_pelt_rejects_short_input_and_dimension_mismatch() -> None:
    assert pelt_boundaries([0.0, 1.0, 0.0], penalty=1.0, min_size=2) == []
    with pytest.raises(ValueError):
        pelt_boundaries([(0.0,), (1.0, 2.0), (0.0,)], penalty=1.0)


def test_cosine_similarity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

