"""Build a self-contained thumbnail + Gemma text HTML gallery."""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path


def _text(value: object) -> str:
    return html.escape(str(value or ""))


def render_gallery(payload: dict[str, object], thumbnails: list[str]) -> str:
    inferences = list(payload.get("inferences", []))
    cards = []
    for index, inference in enumerate(inferences):
        image = thumbnails[index] if index < len(thumbnails) else ""
        during = bool(inference.get("completed_during_stream"))
        badge = "映像中に生成" if during else "映像終了後"
        badge_class = "live" if during else "after"
        objects = "、".join(str(item) for item in inference.get("objects", [])) or "—"
        cards.append(f"""
        <article class="fragment-card">
          <div class="visual">
            <img src="data:image/jpeg;base64,{image}" alt="Fragment {index + 1} thumbnail">
            <div class="timecode">映像 {_text(round(inference.get('source_timestamp_ms', 0) / 1000, 2))} 秒</div>
          </div>
          <div class="description">
            <div class="meta">
              <span class="revision">Revision {_text(inference.get('revision'))}</span>
              <span class="badge {badge_class}">{badge}</span>
              <span>生成 {_text(round(inference.get('completed_wall_ms', 0) / 1000, 2))} 秒</span>
              <span>推論 {_text(round(inference.get('latency_ms', 0) / 1000, 2))} 秒</span>
            </div>
            <h2>{_text(inference.get('observation_ja'))}</h2>
            <dl>
              <dt>物体</dt><dd>{_text(objects)}</dd>
              <dt>行動</dt><dd>{_text(inference.get('action_ja')) or '—'}</dd>
              <dt>前回からの変化</dt><dd>{_text(inference.get('change_from_previous_ja')) or '—'}</dd>
              <dt>確信度</dt><dd>{_text(inference.get('confidence'))}</dd>
            </dl>
          </div>
        </article>""")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI NAS Fragment Gallery</title>
<style>
  :root {{ color-scheme: dark; font-family: "Yu Gothic UI", "Meiryo", sans-serif; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0d1117; color: #e6edf3; }}
  header {{ position: sticky; top: 0; z-index: 2; padding: 18px 28px; background: #161b22ee;
            backdrop-filter: blur(8px); border-bottom: 1px solid #30363d; }}
  header h1 {{ margin: 0 0 7px; font-size: 22px; }}
  header p {{ margin: 0; color: #9da7b3; }}
  main {{ max-width: 1240px; margin: 0 auto; padding: 24px; display: grid; gap: 18px; }}
  .fragment-card {{ display: grid; grid-template-columns: minmax(320px, 46%) 1fr; overflow: hidden;
                    background: #161b22; border: 1px solid #30363d; border-radius: 14px;
                    box-shadow: 0 8px 28px #0005; }}
  .visual {{ position: relative; min-height: 230px; background: #05070a; }}
  .visual img {{ width: 100%; height: 100%; min-height: 230px; object-fit: cover; display: block; }}
  .timecode {{ position: absolute; left: 12px; bottom: 12px; padding: 5px 9px; border-radius: 7px;
               background: #000c; font-variant-numeric: tabular-nums; }}
  .description {{ padding: 20px 22px; }}
  .description h2 {{ margin: 18px 0; font-size: 20px; line-height: 1.65; font-weight: 600; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 8px; color: #9da7b3; font-size: 13px; }}
  .meta span {{ padding: 4px 8px; border-radius: 999px; background: #21262d; }}
  .meta .live {{ color: #71e6a4; background: #123623; }}
  .meta .after {{ color: #f1c16b; background: #3a2a10; }}
  .meta .revision {{ color: #8cc8ff; }}
  dl {{ display: grid; grid-template-columns: 8em 1fr; margin: 0; line-height: 1.7; }}
  dt {{ color: #8b949e; }} dd {{ margin: 0; }}
  @media (max-width: 760px) {{
    .fragment-card {{ grid-template-columns: 1fr; }}
    .visual, .visual img {{ min-height: 0; }}
  }}
</style>
</head>
<body>
<header>
  <h1>AI NAS — Fragment画像とGemmaテキスト</h1>
  <p>{_text(Path(str(payload.get('source', ''))).name)} ／ {len(inferences)}件 ／
     映像時間 {_text(payload.get('stream_wall_seconds'))}秒 ／ 古いRevision置換 {_text(payload.get('queue_replaced'))}件</p>
</header>
<main>{''.join(cards)}</main>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/live-gemma-fragment-gallery.html"))
    args = parser.parse_args()
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    source = Path(payload["source"])

    import cv2

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {source}")
    thumbnails = []
    for inference in payload.get("inferences", []):
        capture.set(cv2.CAP_PROP_POS_MSEC, float(inference["source_timestamp_ms"]))
        ok, frame = capture.read()
        if not ok:
            thumbnails.append("")
            continue
        frame = cv2.resize(frame, (640, 360))
        encoded_ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        thumbnails.append(base64.b64encode(encoded.tobytes()).decode("ascii") if encoded_ok else "")
    capture.release()
    args.output.write_text(render_gallery(payload, thumbnails), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
