"""Add grounded multi-Fragment Scene summaries to a live experiment JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scene_semantics import LMStudioSceneSummaryClient, summarize_payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="gemma4-12b-qat")
    parser.add_argument("--lm-studio-url", default="http://127.0.0.1:1234")
    parser.add_argument(
        "--boundary-ms",
        type=int,
        action="append",
        default=[],
        help="Add a reviewed Scene start boundary; repeat for multiple Scenes",
    )
    args = parser.parse_args()
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    if args.boundary_ms:
        segmentation = payload.setdefault("scene_segmentation", {})
        segmentation["manual_boundaries_ms"] = sorted({
            value for value in args.boundary_ms if value > 0
        })
    client = LMStudioSceneSummaryClient(base_url=args.lm_studio_url, model=args.model)
    payload["scene_summaries"] = summarize_payload(payload, client)
    output = args.output or args.result.with_name(f"{args.result.stem}-scenes.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "scenes": len(payload["scene_summaries"]),
        "gemma_summaries": sum(item.get("method") == "gemma_multi_fragment" for item in payload["scene_summaries"]),
        "output": str(output.resolve()),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
