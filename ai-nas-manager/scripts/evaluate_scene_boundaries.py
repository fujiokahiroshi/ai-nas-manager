"""Compare Scene detectors with reviewed markers and tune Adaptive Memory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scene_evaluation import extract_boundary_sets, match_boundaries, tune_adaptive_memory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--tolerance-ms", type=int, default=1_500)
    parser.add_argument("--output", type=Path, default=Path("docs/scene-boundary-evaluation.json"))
    parser.add_argument("--annotated-output", type=Path)
    args = parser.parse_args()

    result = json.loads(args.result.read_text(encoding="utf-8"))
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    expected = [int(value) for value in truth["boundaries_ms"]]
    boundary_sets = extract_boundary_sets(result)
    comparison = {
        name: match_boundaries(values, expected, tolerance_ms=args.tolerance_ms)
        for name, values in boundary_sets.items()
    }
    adaptive = (
        result.get("scene_segmentation", {})
        .get("shadow_algorithms", {})
        .get("adaptive_memory_v1", {})
    )
    tuned = tune_adaptive_memory(
        list(adaptive.get("state_trace", [])),
        expected,
        tolerance_ms=args.tolerance_ms,
    )
    report = {
        "source": result.get("source"),
        "ground_truth": truth,
        "tolerance_ms": args.tolerance_ms,
        "comparison": comparison,
        "tuned_adaptive_memory": tuned,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.annotated_output:
        result["scene_ground_truth"] = truth
        result["scene_evaluation"] = report
        shadow = result.setdefault("scene_segmentation", {}).setdefault("shadow_algorithms", {})
        shadow["adaptive_memory_tuned_v1"] = {
            "authoritative": False,
            **tuned,
        }
        args.annotated_output.parent.mkdir(parents=True, exist_ok=True)
        args.annotated_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    for name, metrics in comparison.items():
        print(f"{name}: F1={metrics['f1']:.3f} TP={metrics['true_positive']} FP={metrics['false_positive']} FN={metrics['false_negative']}")
    metrics = tuned["metrics"]
    print(f"tuned_adaptive: F1={metrics['f1']:.3f} TP={metrics['true_positive']} FP={metrics['false_positive']} FN={metrics['false_negative']}")
    print(f"output={args.output.resolve()}")


if __name__ == "__main__":
    main()
