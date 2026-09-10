"""Compare fragment experiment JSON with a common ground-truth manifest."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fragment_evaluation import aggregate, evaluate_item, index_results, render_markdown


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--tolerance-ms", type=int, default=1_000)
    parser.add_argument("--importance-threshold", type=float)
    parser.add_argument("--importance-fraction", type=float, default=0.15)
    parser.add_argument("--output", type=Path, default=Path("fragment-evaluation.json"))
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    experiment = json.loads(args.experiment.read_text(encoding="utf-8"))
    predictions = index_results(experiment["results"])
    evaluated = []
    missing = []
    for item in manifest["items"]:
        prediction = predictions.get(Path(item["video"]).name.casefold())
        if prediction is None:
            missing.append(item["id"])
            continue
        evaluated.append(evaluate_item(
            item,
            prediction,
            tolerance_ms=args.tolerance_ms,
            importance_threshold=args.importance_threshold,
            importance_fraction=args.importance_fraction,
        ))

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in evaluated:
        grouped[str(item["dataset"])].append(item)
    report = {
        "algorithm": experiment.get("algorithm"),
        "tolerance_ms": args.tolerance_ms,
        "importance_threshold": args.importance_threshold,
        "importance_fraction": args.importance_fraction,
        "datasets": {name: aggregate(items) for name, items in sorted(grouped.items())},
        "overall": aggregate(evaluated),
        "missing": missing,
        "results": evaluated,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = args.markdown or args.output.with_suffix(".md")
    markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report), end="")
    if missing:
        print(f"Missing predictions: {len(missing)}")
    print(f"JSON: {args.output.resolve()}")
    print(f"Markdown: {markdown.resolve()}")


if __name__ == "__main__":
    main()
