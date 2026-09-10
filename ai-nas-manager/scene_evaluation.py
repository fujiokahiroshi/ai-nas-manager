"""Evaluate and tune causal Scene boundaries without changing production output."""

from __future__ import annotations

from dataclasses import asdict
from itertools import product
from statistics import mean
from typing import Iterable, Sequence

from scene_segmentation import AdaptiveMemoryConfig, AdaptiveMemoryShadowDetector


def match_boundaries(
    predicted_ms: Iterable[int],
    expected_ms: Iterable[int],
    *,
    tolerance_ms: int = 1_500,
) -> dict[str, object]:
    """One-to-one nearest matching with boundary error and classification metrics."""

    predicted = sorted(set(int(value) for value in predicted_ms if int(value) > 0))
    expected = sorted(set(int(value) for value in expected_ms if int(value) > 0))
    unmatched = set(range(len(expected)))
    matches: list[dict[str, int]] = []
    false_positive: list[int] = []
    for value in predicted:
        candidates = [index for index in unmatched if abs(value - expected[index]) <= tolerance_ms]
        if not candidates:
            false_positive.append(value)
            continue
        index = min(candidates, key=lambda item: abs(value - expected[item]))
        unmatched.remove(index)
        matches.append({
            "predicted_ms": value,
            "expected_ms": expected[index],
            "error_ms": value - expected[index],
        })
    false_negative = [expected[index] for index in sorted(unmatched)]
    true_positive_count = len(matches)
    precision = true_positive_count / len(predicted) if predicted else 0.0
    recall = true_positive_count / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    errors = [abs(item["error_ms"]) for item in matches]
    return {
        "predicted": predicted,
        "expected": expected,
        "matches": matches,
        "false_positive_ms": false_positive,
        "false_negative_ms": false_negative,
        "true_positive": true_positive_count,
        "false_positive": len(false_positive),
        "false_negative": len(false_negative),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "mean_absolute_error_ms": round(mean(errors), 3) if errors else None,
    }


def run_adaptive_memory(
    state_trace: Sequence[dict[str, object]],
    config: AdaptiveMemoryConfig,
) -> list[dict[str, object]]:
    detector = AdaptiveMemoryShadowDetector(config)
    events = []
    for item in state_trace:
        events.extend(detector.process(
            int(item["timestamp_ms"]),
            [float(value) for value in item["vector"]],  # type: ignore[index]
        ))
    return [event.as_dict() for event in events]


def tune_adaptive_memory(
    state_trace: Sequence[dict[str, object]],
    expected_ms: Sequence[int],
    *,
    tolerance_ms: int = 1_500,
) -> dict[str, object]:
    """Grid-search only Shadow parameters and return the best reproducible candidate."""

    if not state_trace:
        raise ValueError("state_trace is required for Adaptive Memory tuning")
    candidates = []
    for high, low_ratio, confirmation, min_scene, cooldown in product(
        (0.12, 0.16, 0.20, 0.24, 0.28, 0.32),
        (0.45, 0.65, 0.80),
        (2, 3),
        (1_000, 2_000, 3_000, 5_000),
        (1_000, 2_000, 3_000),
    ):
        config = AdaptiveMemoryConfig(
            high_threshold=high,
            low_threshold=round(high * low_ratio, 6),
            confirmation_samples=confirmation,
            min_scene_ms=min_scene,
            cooldown_ms=cooldown,
        )
        events = run_adaptive_memory(state_trace, config)
        metrics = match_boundaries(
            [int(event["boundary_ms"]) for event in events],
            expected_ms,
            tolerance_ms=tolerance_ms,
        )
        average_error = metrics["mean_absolute_error_ms"]
        candidates.append((
            (
                float(metrics["f1"]),
                float(metrics["recall"]),
                float(metrics["precision"]),
                -(float(average_error) if average_error is not None else 10**12),
                -abs(len(events) - len(expected_ms)),
                -abs(confirmation - 2),
                high,
                -abs(min_scene - 3_000),
                -abs(cooldown - 2_000),
            ),
            config,
            events,
            metrics,
        ))
    _, config, events, metrics = max(candidates, key=lambda item: item[0])
    return {
        "algorithm": "adaptive-memory-v1-grid-search",
        "evaluated_configurations": len(candidates),
        "config": asdict(config),
        "boundaries": events,
        "metrics": metrics,
    }


def extract_boundary_sets(result: dict[str, object]) -> dict[str, list[int]]:
    segmentation = result.get("scene_segmentation", {})
    if not isinstance(segmentation, dict):
        segmentation = {}
    shadow = segmentation.get("shadow_algorithms", {})
    if not isinstance(shadow, dict):
        shadow = {}
    adaptive = shadow.get("adaptive_memory_v1", {})
    if not isinstance(adaptive, dict):
        adaptive = {}
    return {
        "current_online": [
            int(item["boundary_ms"])
            for item in segmentation.get("online_boundaries", [])
        ],
        "pelt_final": [int(value) for value in segmentation.get("pelt_boundaries_ms", [])],
        "adaptive_shadow": [
            int(item["boundary_ms"])
            for item in adaptive.get("boundaries", [])
        ],
    }
