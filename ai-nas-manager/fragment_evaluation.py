"""Metrics and a common manifest format for Fragment detector experiments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TimeInterval:
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("invalid time interval")

    def accepts(self, timestamp_ms: int, tolerance_ms: int = 0) -> bool:
        return self.start_ms - tolerance_ms <= timestamp_ms <= self.end_ms + tolerance_ms


def match_points_to_intervals(
    predicted_ms: Iterable[int],
    expected: Iterable[TimeInterval],
    *,
    tolerance_ms: int = 1_000,
) -> tuple[int, int, int]:
    """Return one-to-one true-positive, false-positive and false-negative counts."""

    predictions = sorted(set(int(value) for value in predicted_ms))
    intervals = list(expected)
    unmatched = set(range(len(intervals)))
    true_positive = 0
    for timestamp in predictions:
        candidates = [
            index for index in unmatched
            if intervals[index].accepts(timestamp, tolerance_ms)
        ]
        if not candidates:
            continue
        closest = min(
            candidates,
            key=lambda index: abs(
                timestamp - (intervals[index].start_ms + intervals[index].end_ms) // 2
            ),
        )
        unmatched.remove(closest)
        true_positive += 1
    return true_positive, len(predictions) - true_positive, len(unmatched)


def classification_metrics(true_positive: int, false_positive: int, false_negative: int) -> dict[str, float | int]:
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


_NON_SEMANTIC_REASONS = {
    "stream_start",
    "continued_stream",
    "maximum_duration",
    "quiet_timeout",
    "end_of_stream",
    "source_disconnected",
}


def prediction_points(events: list[dict[str, object]]) -> tuple[list[int], list[int]]:
    """Extract boundary points and meaningful online marker points."""

    opens = [event for event in events if event.get("kind") == "open"]
    boundaries = [int(event["start_ms"]) for event in opens if int(event["start_ms"]) > 0]
    markers = [
        int(event["observed_ms"])
        for event in events
        if event.get("kind") in {"open", "update"}
        and event.get("reason") not in _NON_SEMANTIC_REASONS
    ]
    return boundaries, markers


def evaluate_item(
    item: dict[str, object],
    result: dict[str, object],
    *,
    tolerance_ms: int = 1_000,
    importance_threshold: float | None = None,
    importance_fraction: float = 0.15,
) -> dict[str, object]:
    boundaries, markers = prediction_points(result.get("events", []))  # type: ignore[arg-type]
    boundary_intervals = [TimeInterval(*pair) for pair in item.get("boundaries_ms", [])]  # type: ignore[arg-type]
    importance_entries = list(item.get("importance", []))  # type: ignore[arg-type]
    selection = item.get("importance_selection", "ranked")
    if selection == "all":
        selected = importance_entries
    elif importance_threshold is None:
        keep = max(1, round(len(importance_entries) * importance_fraction)) if importance_entries else 0
        selected = sorted(importance_entries, key=lambda entry: float(entry["score"]), reverse=True)[:keep]
    else:
        selected = [entry for entry in importance_entries if float(entry["score"]) >= importance_threshold]
    important_intervals = [
        TimeInterval(int(entry["start_ms"]), int(entry["end_ms"])) for entry in selected
    ]
    tasks = set(item.get("tasks", ["boundary", "importance"]))
    boundary_counts = match_points_to_intervals(boundaries, boundary_intervals, tolerance_ms=tolerance_ms)
    importance_counts = match_points_to_intervals(markers, important_intervals, tolerance_ms=tolerance_ms)
    duration_ms = int(item.get("duration_ms", result.get("duration_ms", 0)))
    false_markers = importance_counts[1]
    return {
        "id": item["id"],
        "dataset": item["dataset"],
        "video": item["video"],
        "tasks": sorted(tasks),
        "duration_ms": duration_ms,
        "predicted_boundaries": len(boundaries),
        "predicted_markers": len(markers),
        "boundary": classification_metrics(*boundary_counts) if "boundary" in tasks else None,
        "importance": classification_metrics(*importance_counts) if "importance" in tasks else None,
        "false_markers_per_hour": (
            round(false_markers * 3_600_000 / duration_ms, 3)
            if duration_ms and "importance" in tasks else None
        ),
        "processing_seconds": result.get("processing_seconds"),
        "realtime_factor": result.get("realtime_factor"),
    }


def aggregate(items: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {"items": len(items)}
    for task in ("boundary", "importance"):
        applicable = [item for item in items if item[task] is not None]
        if not applicable:
            output[task] = None
            continue
        tp = sum(int(item[task]["true_positive"]) for item in applicable)  # type: ignore[index]
        fp = sum(int(item[task]["false_positive"]) for item in applicable)  # type: ignore[index]
        fn = sum(int(item[task]["false_negative"]) for item in applicable)  # type: ignore[index]
        output[task] = classification_metrics(tp, fp, fn)
    importance_items = [item for item in items if item["importance"] is not None]
    duration_ms = sum(int(item["duration_ms"]) for item in importance_items)
    false_markers = sum(int(item["importance"]["false_positive"]) for item in importance_items)  # type: ignore[index]
    output["duration_hours"] = round(sum(int(item["duration_ms"]) for item in items) / 3_600_000, 6)
    output["false_markers_per_hour"] = round(false_markers * 3_600_000 / duration_ms, 3) if duration_ms else None
    return output


def render_markdown(report: dict[str, object]) -> str:
    rows = [
        "# Fragment evaluation",
        "",
        "| Dataset | Videos | Boundary F1 | Important-event recall | False markers/hour |",
        "|---|---:|---:|---:|---:|",
    ]
    metric = lambda value, name: "N/A" if value[name] is None else f"{value[name]['f1' if name == 'boundary' else 'recall']:.3f}"
    for dataset, summary in report["datasets"].items():  # type: ignore[union-attr]
        rows.append(
            f"| {dataset} | {summary['items']} | {metric(summary, 'boundary')} | "
            f"{metric(summary, 'importance')} | {summary['false_markers_per_hour'] if summary['false_markers_per_hour'] is not None else 'N/A'} |"
        )
    overall = report["overall"]
    rows.extend([
        f"| **Overall** | **{overall['items']}** | **{metric(overall, 'boundary')}** | "
        f"**{metric(overall, 'importance')}** | **{overall['false_markers_per_hour'] if overall['false_markers_per_hour'] is not None else 'N/A'}** |",
        "",
        f"Boundary matching tolerance: ±{report['tolerance_ms']} ms. ",
        (
            f"Important segments use score >= {report['importance_threshold']}."
            if report["importance_threshold"] is not None
            else (
                f"Ranked importance data uses the top {report['importance_fraction']:.0%} per video; "
                "manifests marked importance_selection=all use every annotated interval."
            )
        ),
    ])
    rows[-2] = f"Boundary matching tolerance: +/-{report['tolerance_ms']} ms."
    return "\n".join(rows) + "\n"


def index_results(results: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {Path(str(result["source"])).name.casefold(): result for result in results}
