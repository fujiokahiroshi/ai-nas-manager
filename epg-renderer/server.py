"""Windows側でネイティブに動く、EPG(番組表)専用の表示rendererのMCPサーバー。

WSLには一切依存しない。Claudeがai-nas-manager(WSL側)から取得した番組表データを
そのまま渡すと、チャンネル×時間の格子HTMLを生成して既定ブラウザで開く。
現在時刻ラインと、番組クリックでの詳細パネル表示に対応する。
"""

from __future__ import annotations

import html
import json
import tempfile
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("epg-renderer")

_GENRE_COLORS = {
    "ニュース": "#3b6ea5",
    "報道": "#3b6ea5",
    "ドラマ": "#a5476b",
    "バラエティ": "#c98a2c",
    "情報": "#2c9c7a",
    "映画": "#6a4ca5",
    "ドキュメンタリー": "#4c8a4c",
    "アニメ": "#c94c8a",
    "スポーツ": "#2c7ac9",
}
_DEFAULT_COLOR = "#6b7280"
_GRID_HEIGHT_PX = 720


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def _build_html(programs: list[dict[str, Any]]) -> str:
    if not programs:
        return "<!doctype html><html><body><p>番組データがありません。</p></body></html>"

    channels: list[str] = []
    for p in programs:
        if p["channel"] not in channels:
            channels.append(p["channel"])

    starts = [_parse(p["start"]) for p in programs]
    ends = [_parse(p["end"]) for p in programs]
    timeline_start = min(starts)
    timeline_end = max(ends)
    total_seconds = max((timeline_end - timeline_start).total_seconds(), 1.0)

    by_channel: dict[str, list[dict[str, Any]]] = {c: [] for c in channels}
    for p in programs:
        by_channel[p["channel"]].append(p)

    columns_html: list[str] = []
    for channel in channels:
        blocks: list[str] = []
        for p in sorted(by_channel[channel], key=lambda x: x["start"]):
            start = _parse(p["start"])
            end = _parse(p["end"])
            top_pct = (start - timeline_start).total_seconds() / total_seconds * 100
            height_pct = (end - start).total_seconds() / total_seconds * 100
            color = _GENRE_COLORS.get(p.get("genre", ""), _DEFAULT_COLOR)
            title = html.escape(str(p.get("title", "")))
            time_label = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
            blocks.append(
                f'<div class="program" style="top:{top_pct:.3f}%;height:{height_pct:.3f}%;'
                f'background:{color};" '
                f'data-channel="{_attr(str(p.get("channel", "")))}" '
                f'data-title="{_attr(str(p.get("title", "")))}" '
                f'data-start="{_attr(str(p.get("start", "")))}" '
                f'data-end="{_attr(str(p.get("end", "")))}" '
                f'data-genre="{_attr(str(p.get("genre", "")))}" '
                f'data-description="{_attr(str(p.get("description", "")))}">'
                f'<div class="program-time">{time_label}</div>'
                f'<div class="program-title">{title}</div>'
                f"</div>"
            )
        columns_html.append(
            f'<div class="channel-col"><div class="channel-header">{html.escape(channel)}</div>'
            f'<div class="channel-body">{"".join(blocks)}</div></div>'
        )

    ticks: list[str] = []
    first_minute = 0 if timeline_start.minute < 30 else 30
    cursor = timeline_start.replace(minute=first_minute, second=0, microsecond=0)
    while cursor <= timeline_end:
        top_pct = (cursor - timeline_start).total_seconds() / total_seconds * 100
        if 0 <= top_pct <= 100:
            ticks.append(f'<div class="tick" style="top:{top_pct:.3f}%">{cursor.strftime("%H:%M")}</div>')
        cursor += timedelta(minutes=30)

    timeline_start_js = json.dumps(timeline_start.isoformat())
    total_seconds_js = json.dumps(total_seconds)

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>番組表</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 24px; background: #0b0f14; color: #e5e7eb; }}
  h1 {{ font-size: 18px; margin: 0 0 16px; }}
  .guide {{ display: flex; position: relative; border: 1px solid #2a3441; border-radius: 8px; overflow: hidden; }}
  .time-axis {{ position: relative; width: 56px; flex-shrink: 0; border-right: 1px solid #2a3441; height: {_GRID_HEIGHT_PX}px; }}
  .tick {{ position: absolute; left: 0; right: 0; font-size: 11px; color: #9ca3af; transform: translateY(-50%); padding-left: 6px; border-top: 1px dashed #263041; }}
  .channel-col {{ flex: 1; border-right: 1px solid #2a3441; min-width: 160px; }}
  .channel-col:last-child {{ border-right: none; }}
  .channel-header {{ height: 32px; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 13px; background: #131a24; border-bottom: 1px solid #2a3441; }}
  .channel-body {{ position: relative; height: {_GRID_HEIGHT_PX}px; }}
  .program {{ position: absolute; left: 2px; right: 2px; border-radius: 4px; padding: 4px 6px; overflow: hidden; box-sizing: border-box; border: 1px solid rgba(255,255,255,0.15); cursor: pointer; }}
  .program:hover {{ filter: brightness(1.15); }}
  .program.selected {{ outline: 2px solid #ffffff; outline-offset: -2px; }}
  .program-time {{ font-size: 10px; opacity: 0.85; }}
  .program-title {{ font-size: 12px; font-weight: 600; line-height: 1.2; }}
  .now-line {{ position: absolute; left: 0; right: 0; height: 0; border-top: 2px solid #ff4d4f; z-index: 5; display: none; pointer-events: none; }}
  .now-line::before {{ content: "now"; position: absolute; left: -2px; top: -8px; background: #ff4d4f; color: #fff; font-size: 10px; padding: 1px 4px; border-radius: 3px; transform: translateY(-100%); }}
  .detail-panel {{ margin-top: 16px; border: 1px solid #2a3441; border-radius: 8px; padding: 16px; background: #131a24; min-height: 96px; }}
  .detail-panel .placeholder {{ color: #6b7280; font-size: 13px; }}
  .detail-panel .detail-title {{ font-size: 16px; font-weight: 700; margin-bottom: 4px; }}
  .detail-panel .detail-meta {{ font-size: 12px; color: #9ca3af; margin-bottom: 10px; }}
  .detail-panel .detail-desc {{ font-size: 13px; line-height: 1.6; }}
  .genre-badge {{ display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 999px; margin-right: 8px; }}
</style>
</head>
<body>
  <h1>番組表 ({timeline_start.strftime('%Y-%m-%d')})</h1>
  <div class="guide" id="guide">
    <div class="time-axis">{"".join(ticks)}</div>
    {"".join(columns_html)}
    <div class="now-line" id="now-line"></div>
  </div>
  <div class="detail-panel" id="detail-panel">
    <div class="placeholder">番組をクリックすると詳細が表示されます。</div>
  </div>
<script>
  const TIMELINE_START = new Date({timeline_start_js});
  const TOTAL_SECONDS = {total_seconds_js};

  function updateNowLine() {{
    const line = document.getElementById('now-line');
    const elapsed = (new Date() - TIMELINE_START) / 1000;
    if (elapsed < 0 || elapsed > TOTAL_SECONDS) {{
      line.style.display = 'none';
      return;
    }}
    line.style.display = 'block';
    line.style.top = (elapsed / TOTAL_SECONDS * 100) + '%';
  }}
  updateNowLine();
  setInterval(updateNowLine, 30000);

  const genreColors = {json.dumps(_GENRE_COLORS)};

  document.getElementById('guide').addEventListener('click', (event) => {{
    const el = event.target.closest('.program');
    if (!el) return;

    document.querySelectorAll('.program.selected').forEach((p) => p.classList.remove('selected'));
    el.classList.add('selected');

    const genre = el.dataset.genre || '';
    const color = genreColors[genre] || '{_DEFAULT_COLOR}';
    const start = el.dataset.start.slice(11, 16);
    const end = el.dataset.end.slice(11, 16);

    document.getElementById('detail-panel').innerHTML = `
      <div class="detail-title">${{el.dataset.title}}</div>
      <div class="detail-meta">
        <span class="genre-badge" style="background:${{color}}">${{genre}}</span>
        ${{el.dataset.channel}} ・ ${{start}}–${{end}}
      </div>
      <div class="detail-desc">${{el.dataset.description}}</div>
    `;
  }});
</script>
</body>
</html>"""


@mcp.tool()
def render_epg(programs: list[dict[str, Any]]) -> str:
    """番組表データ(get_tuner_program_guideの戻り値そのもの)を受け取り、
    チャンネル×時間の格子HTMLを生成して既定ブラウザで開く。
    現在時刻の赤線表示と、番組クリックでの詳細パネル表示に対応する。"""
    html_content = _build_html(programs)
    tmp_dir = Path(tempfile.gettempdir()) / "epg-renderer"
    tmp_dir.mkdir(exist_ok=True)
    out_path = tmp_dir / "epg.html"
    out_path.write_text(html_content, encoding="utf-8")
    webbrowser.open(out_path.as_uri())
    return f"番組表を表示しました: {out_path}"


if __name__ == "__main__":
    mcp.run()
