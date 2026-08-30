"""Windows側でネイティブに動く、picture/movie再生専用のrendererのMCPサーバー。

WSLには一切依存しない。Claudeがai-nas-manager(WSL側)から取得したメディアの場所
(UNCパス)を渡すと、既定ブラウザでプレイヤーページを表示・制御する。

設計ドキュメント: ai-nas-manager/docs/media-renderer-design.md
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("media-renderer")

HTTP_HOST = "127.0.0.1"
HTTP_PORT = int(os.environ.get("MEDIA_RENDERER_HTTP_PORT", "39231"))
LEADER_BASE_URL = f"http://{HTTP_HOST}:{HTTP_PORT}"

# タブが「生きている」とみなす、/stateポーリング(1秒間隔)からの許容経過時間。
# 設計ドキュメント4.4節: ポーリング間隔の5倍を閾値にする。
TAB_ALIVE_TIMEOUT_SEC = 5.0

_PLAYER_DIR = Path(tempfile.gettempdir()) / "media-renderer"
_PLAYER_HTML_PATH = _PLAYER_DIR / "player.html"
_CHOICES_HTML_PATH = _PLAYER_DIR / "chooser.html"
_PICTURE_HTML_PATH = _PLAYER_DIR / "picture.html"

# Claude CodeとClaude Desktopなど、同じMCPサーバー定義から複数プロセスが同時に
# 起動されることがある(windows-message-mcpで経験済みの問題)。固定ポートを
# 取得できたプロセスだけが「リーダー」として実際の再生状態を保持し、
# 取得できなかった「フォロワー」はツール呼び出しをリーダーへHTTP転送する。
is_leader = False

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "source_type": None,
    "source_value": None,
    "command": "stop",
    "seq": 0,
    "channel": None,
    "title": None,
    "tag": None,
    "thumbnail": None,
    "seek_to": None,
}
_last_seen: float | None = None

_choice_lock = threading.Lock()
_choice_state: dict[str, Any] = {
    "options": [],
    "seq": 0,
    "selected_index": None,
}
_choice_last_seen: float | None = None

_picture_lock = threading.Lock()
_picture_state: dict[str, Any] = {
    "picture_uri": None,
    "seq": 0,
}
_picture_last_seen: float | None = None


def _unc_to_file_uri(unc_path: str) -> str:
    """WindowsのUNCパス(\\\\wsl.localhost\\Ubuntu\\...)をfile:// URIに変換する。

    pathlibのas_uri()に委譲することで、スペースや日本語ファイル名を含む場合の
    パーセントエンコーディングも正しく行われる。
    """
    return PureWindowsPath(unc_path).as_uri()


def _player_html() -> str:
    state_url = json.dumps(LEADER_BASE_URL + "/state")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>media_renderer player</title>
<style>
  html, body {{ margin:0; height:100%; background:#000; overflow:hidden; }}
  #overlay {{
    position:fixed; inset:0; background:rgba(0,0,0,0.85); color:#eee;
    display:flex; align-items:center; justify-content:center;
    font-family:sans-serif; font-size:28px; cursor:pointer; z-index:10;
  }}
  video {{ width:100vw; height:100vh; object-fit:contain; background:#000; display:block; }}
  #title {{
    position:fixed; top:8px; left:8px; color:#fff; font-family:sans-serif;
    font-size:14px; opacity:0.7; z-index:5; text-shadow:0 0 4px #000;
  }}
  #tag {{
    position:fixed; left:8px; right:8px; top:32px; color:#fff;
    font-family:sans-serif; font-size:13px; line-height:1.5; z-index:5;
    background:rgba(0,0,0,0.55); padding:8px 12px; border-radius:6px;
    display:none;
  }}
  #tag:not(:empty) {{ display:block; }}
  #thumbnail {{
    position:fixed; right:12px; bottom:12px; width:220px; height:160px;
    object-fit:contain; border:2px solid rgba(255,255,255,0.85); border-radius:6px;
    z-index:6; box-shadow:0 2px 10px rgba(0,0,0,0.6); display:none; background:#000;
  }}
</style>
</head>
<body>
  <div id="overlay">&#9654; クリックして開始</div>
  <video id="player" controls></video>
  <div id="title"></div>
  <div id="tag"></div>
  <img id="thumbnail" alt="thumbnail">
<script>
  const STATE_URL = {state_url};
  const video = document.getElementById('player');
  const overlay = document.getElementById('overlay');
  const titleEl = document.getElementById('title');
  const tagEl = document.getElementById('tag');
  const thumbEl = document.getElementById('thumbnail');
  let started = false;
  let lastSeq = -1;
  let currentState = null;

  function reportBrowserState(command) {{
    fetch(STATE_URL, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{command}})
    }}).catch(() => {{}});
  }}

  function applySeek(target) {{
    if (typeof target !== 'number') return;
    if (video.readyState >= 1) {{
      video.currentTime = target;
    }} else {{
      video.addEventListener('loadedmetadata', () => {{ video.currentTime = target; }}, {{ once: true }});
    }}
  }}

  function applyThumbnail(data) {{
    if (data.thumbnail) {{
      if (thumbEl.src !== data.thumbnail) {{
        thumbEl.src = data.thumbnail;
      }}
      thumbEl.style.display = 'block';
    }} else {{
      thumbEl.style.display = 'none';
      thumbEl.removeAttribute('src');
    }}
  }}

  function applyState(data) {{
    titleEl.textContent = data.title || '';
    tagEl.textContent = data.tag || '';
    applyThumbnail(data);
    if (data.command === 'play' && data.source_value) {{
      if (video.dataset.src !== data.source_value) {{
        video.src = data.source_value;
        video.dataset.src = data.source_value;
      }}
      video.dataset.ignorePause = '1';
      video.play().catch(() => {{}});
      setTimeout(() => {{ video.dataset.ignorePause = '0'; }}, 0);
    }} else if (data.command === 'stop') {{
      video.dataset.ignorePause = '1';
      video.pause();
      setTimeout(() => {{ video.dataset.ignorePause = '0'; }}, 0);
    }}
    applySeek(data.seek_to);
  }}

  overlay.addEventListener('click', () => {{
    started = true;
    overlay.style.display = 'none';
    if (currentState) {{
      lastSeq = currentState.seq;
      applyState(currentState);
    }}
  }});

  video.addEventListener('pause', () => {{
    if (video.dataset.ignorePause === '1') return;
    reportBrowserState('stop');
  }});

  async function poll() {{
    try {{
      const res = await fetch(STATE_URL, {{cache: 'no-store'}});
      const data = await res.json();
      currentState = data;
      titleEl.textContent = data.title || '';
      tagEl.textContent = data.tag || '';
      applyThumbnail(data);
      if (started && data.seq !== lastSeq) {{
        lastSeq = data.seq;
        applyState(data);
      }}
    }} catch (e) {{
      /* リーダー未応答。次回ポーリングで再試行する */
    }}
  }}

  setInterval(poll, 1000);
  poll();
</script>
</body>
</html>"""


def _open_player_page() -> None:
    _PLAYER_DIR.mkdir(exist_ok=True)
    _PLAYER_HTML_PATH.write_text(_player_html(), encoding="utf-8")
    webbrowser.open(_PLAYER_HTML_PATH.as_uri())


def _choices_html() -> str:
    choices_url = json.dumps(LEADER_BASE_URL + "/choices")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>media_renderer chooser</title>
<style>
  html, body {{ margin:0; min-height:100%; background:#111; color:#eee; font-family:sans-serif; }}
  #grid {{ display:flex; flex-wrap:wrap; gap:16px; padding:20px; }}
  .card {{
    width:240px; cursor:pointer; border:3px solid transparent; border-radius:8px;
    padding:8px; background:#1c1c1c; transition:border-color .15s;
  }}
  .card:hover {{ border-color:#5b9dff; }}
  .card.selected {{ border-color:#4caf50; background:#123018; }}
  .card img {{ width:100%; height:160px; object-fit:cover; border-radius:4px; background:#000; display:block; }}
  .card .label {{ margin-top:8px; font-size:14px; line-height:1.4; }}
  #empty {{ padding:20px; opacity:0.7; }}
  #status {{ padding:0 20px 20px; font-size:13px; opacity:0.8; }}
</style>
</head>
<body>
  <div id="empty">候補が指定されていません。</div>
  <div id="grid"></div>
  <div id="status"></div>
<script>
  const CHOICES_URL = {choices_url};
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  const status = document.getElementById('status');
  let lastSeq = -1;

  function render(data) {{
    const options = data.options || [];
    empty.style.display = options.length ? 'none' : 'block';
    grid.innerHTML = '';
    options.forEach((opt, i) => {{
      const card = document.createElement('div');
      card.className = 'card' + (data.selected_index === i ? ' selected' : '');
      const img = document.createElement('img');
      img.src = opt.thumbnail_uri;
      img.alt = opt.label || '';
      const label = document.createElement('div');
      label.className = 'label';
      label.textContent = opt.label || ('候補' + (i + 1));
      card.appendChild(img);
      card.appendChild(label);
      card.addEventListener('click', () => choose(i));
      grid.appendChild(card);
    }});
    status.textContent = (data.selected_index !== null && data.selected_index !== undefined)
      ? '選択済み: ' + (options[data.selected_index] ? options[data.selected_index].label : '')
      : 'クリックして選んでください。';
  }}

  function choose(index) {{
    fetch(CHOICES_URL, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{index}})
    }}).catch(() => {{}});
  }}

  async function poll() {{
    try {{
      const res = await fetch(CHOICES_URL, {{cache: 'no-store'}});
      const data = await res.json();
      if (data.seq !== lastSeq) {{
        lastSeq = data.seq;
        render(data);
      }}
    }} catch (e) {{
      /* リーダー未応答。次回ポーリングで再試行する */
    }}
  }}

  setInterval(poll, 800);
  poll();
</script>
</body>
</html>"""


def _open_choices_page() -> None:
    _PLAYER_DIR.mkdir(exist_ok=True)
    _CHOICES_HTML_PATH.write_text(_choices_html(), encoding="utf-8")
    webbrowser.open(_CHOICES_HTML_PATH.as_uri())


def _picture_html() -> str:
    picture_url = json.dumps(LEADER_BASE_URL + "/picture")
    return f"""<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>picture</title>
<style>
  html, body {{ margin:0; height:100%; background:#111; }}
  body {{ display:flex; align-items:center; justify-content:center; }}
  img {{ max-width:100vw; max-height:100vh; object-fit:contain; display:none; }}
</style>
</head>
<body>
<img id="picture" alt="picture">
<script>
  const PICTURE_URL = {picture_url};
  const img = document.getElementById('picture');
  let lastSeq = -1;

  async function poll() {{
    try {{
      const res = await fetch(PICTURE_URL, {{cache: 'no-store'}});
      const data = await res.json();
      if (data.seq !== lastSeq) {{
        lastSeq = data.seq;
        if (data.picture_uri) {{
          img.src = data.picture_uri;
          img.style.display = 'block';
        }} else {{
          img.style.display = 'none';
          img.removeAttribute('src');
        }}
      }}
    }} catch (e) {{
      /* リーダー未応答。次回ポーリングで再試行する */
    }}
  }}

  setInterval(poll, 1000);
  poll();
</script>
</body>
</html>"""


def _open_picture_page() -> None:
    _PLAYER_DIR.mkdir(exist_ok=True)
    _PICTURE_HTML_PATH.write_text(_picture_html(), encoding="utf-8")
    webbrowser.open(_PICTURE_HTML_PATH.as_uri())


def _apply_render_picture(path: str) -> str:
    file_uri = _unc_to_file_uri(path)
    need_new_tab = (
        _picture_last_seen is None
        or (time.time() - _picture_last_seen) > TAB_ALIVE_TIMEOUT_SEC
    )
    with _picture_lock:
        _picture_state["picture_uri"] = file_uri
        _picture_state["seq"] += 1
    if need_new_tab:
        _open_picture_page()
        return f"画像を表示しました: {path}"
    return f"表示中の画像を切り替えました: {path}"


def _apply_play(
    source_type: str,
    source_value: str,
    channel: int | None,
    title: str | None,
    tag: str | None = None,
    seek_seconds: float | None = None,
    thumbnail_path: str | None = None,
) -> str:
    if source_type != "file":
        return f"未対応のsource_type: '{source_type}' (v-01は'file'のみ対応)"

    need_new_tab = (
        _last_seen is None or (time.time() - _last_seen) > TAB_ALIVE_TIMEOUT_SEC
    )
    file_uri = _unc_to_file_uri(source_value)
    thumbnail_uri = _unc_to_file_uri(thumbnail_path) if thumbnail_path else None
    with _state_lock:
        _state["source_type"] = source_type
        _state["source_value"] = file_uri
        _state["channel"] = channel
        _state["title"] = title
        _state["tag"] = tag
        _state["thumbnail"] = thumbnail_uri
        _state["seek_to"] = seek_seconds
        _state["command"] = "play"
        _state["seq"] += 1

    label = title or (f"CH{channel}" if channel is not None else source_value)
    seek_note = f"({seek_seconds:.1f}秒の位置から)" if seek_seconds is not None else ""
    if need_new_tab:
        _open_player_page()
        return (
            f"プレイヤーをブラウザで開き、{label}{seek_note}の再生を指示しました。"
            "初回のみ画面の「クリックして開始」を押してください。"
        )
    return f"{label}{seek_note}の再生に切り替えました。"


def _apply_stop() -> str:
    with _state_lock:
        _state["command"] = "stop"
        _state["seek_to"] = None
        _state["seq"] += 1
    return "停止を指示しました。"


def _apply_seek(seconds: float) -> str:
    """再生中のソースを切り替えずに、指定秒数の位置にシークする。"""
    with _state_lock:
        _state["seek_to"] = seconds
        _state["seq"] += 1
    return f"{seconds:.1f}秒の位置にシークしました。"


def _get_status() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def _apply_render_choices(options: list[dict[str, Any]]) -> str:
    processed: list[dict[str, Any]] = []
    for i, opt in enumerate(options):
        thumbnail_path = opt.get("thumbnail_path")
        source_value = opt.get("source_value")
        if not thumbnail_path:
            return f"{i}番目の候補にthumbnail_pathがありません。"
        if not source_value:
            return f"{i}番目の候補にsource_valueがありません。"
        processed.append(
            {
                "thumbnail_path": thumbnail_path,
                "thumbnail_uri": _unc_to_file_uri(thumbnail_path),
                "label": opt.get("label") or f"候補{i + 1}",
                "source_value": source_value,
                "tag": opt.get("tag"),
                "seek_seconds": opt.get("seek_seconds"),
                "channel": opt.get("channel"),
            }
        )
    need_new_tab = (
        _choice_last_seen is None
        or (time.time() - _choice_last_seen) > TAB_ALIVE_TIMEOUT_SEC
    )
    with _choice_lock:
        _choice_state["options"] = processed
        _choice_state["selected_index"] = None
        _choice_state["seq"] += 1
    if need_new_tab:
        _open_choices_page()
        return f"{len(processed)}件の候補をブラウザに表示しました。選択されたらget_selectionで取得できます。"
    return f"{len(processed)}件の候補に更新しました(既存のタブに反映されます)。"


def _apply_choice(index: int) -> str:
    with _choice_lock:
        options = _choice_state["options"]
        if not (0 <= index < len(options)):
            return f"無効な選択indexです: {index}"
        _choice_state["selected_index"] = index
        _choice_state["seq"] += 1
        label = options[index]["label"]
    return f"「{label}」が選択されました。"


def _get_selection() -> dict[str, Any]:
    with _choice_lock:
        idx = _choice_state["selected_index"]
        if idx is None:
            return {"selected": None}
        opt = dict(_choice_state["options"][idx])
        opt.pop("thumbnail_uri", None)
        return {"selected": {"index": idx, **opt}}


def _apply_browser_report(command: str) -> str:
    """ブラウザ側のユーザー操作で状態が変化したときに内部状態を同期する。"""
    if command not in {"play", "stop"}:
        return f"未対応のコマンド: {command}"
    with _state_lock:
        _state["command"] = command
        _state["seq"] += 1
    if command == "stop":
        return "ブラウザ側で再生停止が検出されました。"
    return "ブラウザ側で再生再開が検出されました。"


def _forward(path: str, body: dict[str, Any]) -> str:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        LEADER_BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            return str(result.get("message", ""))
    except Exception as e:  # noqa: BLE001 - リーダー未応答等をユーザー向けに要約する
        return f"リーダープロセスへの転送に失敗しました: {e}"


class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return dict(json.loads(raw or b"{}"))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandlerの命名規則
        global _last_seen, _choice_last_seen, _picture_last_seen
        if self.path == "/state":
            _last_seen = time.time()
            with _state_lock:
                self._send_json(dict(_state))
            return
        if self.path == "/choices":
            _choice_last_seen = time.time()
            with _choice_lock:
                self._send_json(dict(_choice_state))
            return
        if self.path == "/picture":
            _picture_last_seen = time.time()
            with _picture_lock:
                self._send_json(dict(_picture_state))
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/state":
            try:
                body = self._read_json_body()
            except Exception:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            command = str(body.get("command", "")).strip()
            message = _apply_browser_report(command)
            self._send_json({"message": message})
            return
        if self.path == "/internal/play":
            try:
                body = self._read_json_body()
            except Exception:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            message = _apply_play(
                str(body.get("source_type", "")),
                str(body.get("source_value", "")),
                body.get("channel"),
                body.get("title"),
                body.get("tag"),
                body.get("seek_seconds"),
                body.get("thumbnail_path"),
            )
            self._send_json({"message": message})
            return
        if self.path == "/internal/stop":
            message = _apply_stop()
            self._send_json({"message": message})
            return
        if self.path == "/internal/seek":
            try:
                body = self._read_json_body()
            except Exception:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            message = _apply_seek(float(body.get("seconds", 0.0)))
            self._send_json({"message": message})
            return
        if self.path == "/internal/render_choices":
            try:
                body = self._read_json_body()
            except Exception:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            message = _apply_render_choices(list(body.get("options", [])))
            self._send_json({"message": message})
            return
        if self.path == "/choices":
            try:
                body = self._read_json_body()
            except Exception:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            message = _apply_choice(int(body.get("index", -1)))
            self._send_json({"message": message})
            return
        if self.path == "/internal/render_picture":
            try:
                body = self._read_json_body()
            except Exception:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            message = _apply_render_picture(str(body.get("path", "")))
            self._send_json({"message": message})
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


class _ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """http.server.HTTPServerは既定でallow_reuse_address=1になっており、
    Windowsでは既に他プロセスがbind済みのポートへの2つ目のbindを黙って成功させて
    しまう(POSIXのSO_REUSEADDRとは意味が異なる)。リーダー/フォロワー判定を
    正しく機能させるため、明示的にFalseへ固定する。"""

    allow_reuse_address = False


def _start_control_server() -> None:
    global is_leader
    try:
        httpd = _ExclusiveThreadingHTTPServer((HTTP_HOST, HTTP_PORT), _Handler)
    except OSError:
        is_leader = False
        return
    is_leader = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()


_start_control_server()


@mcp.tool()
def play_channel(
    source_type: str,
    source_value: str,
    channel: int | None = None,
    title: str | None = None,
    tag: str | None = None,
    seek_seconds: float | None = None,
    thumbnail_path: str | None = None,
) -> str:
    """指定したメディアソースを再生する。

    source_type='file'の場合、source_valueはWindowsのUNCパス
    (例: \\\\wsl.localhost\\Ubuntu\\home\\hiroshi\\test\\...)。
    'hls'/'url'は将来対応予定で、v-01では未対応エラーを返す。
    プレイヤーページが未起動(または閉じられている)なら新規に開き、
    既に開いていればチャンネル切り替え(同じタブ内で動画を差し替え)。
    tagは映像の内容を意味理解した説明文(get_media_locationのtagをそのまま
    渡す想定)。指定するとプレイヤー画面下部に表示される。
    seek_secondsを指定すると、その秒数の位置から再生を開始する
    (ai-nas-manager.get_fragment_detailsのstartをそのまま渡せる)。
    thumbnail_pathを指定すると、代表フレーム画像(UNCパス)を画面右下に
    小さく重ねて表示する。
    """
    if is_leader:
        return _apply_play(
            source_type, source_value, channel, title, tag, seek_seconds, thumbnail_path
        )
    return _forward(
        "/internal/play",
        {
            "source_type": source_type,
            "source_value": source_value,
            "channel": channel,
            "title": title,
            "tag": tag,
            "seek_seconds": seek_seconds,
            "thumbnail_path": thumbnail_path,
        },
    )


@mcp.tool()
def stop_media() -> str:
    """再生を停止する。"""
    if is_leader:
        return _apply_stop()
    return _forward("/internal/stop", {})


@mcp.tool()
def seek(position_seconds: float) -> str:
    """再生中のソースを切り替えずに、指定秒数の位置にシークする。

    未再生の状態で呼んでも、再生指示が来た時点で反映される(状態としては
    保持される)。ソース自体を切り替えたい場合はplay_channelのseek_seconds
    引数を使うこと。
    """
    if is_leader:
        return _apply_seek(position_seconds)
    return _forward("/internal/seek", {"seconds": position_seconds})


@mcp.tool()
def get_playback_status() -> dict:
    """現在の再生状態(ソース・チャンネル・タイトル・tag・再生中かどうか)を返す。

    ユーザーがブラウザ側で直接操作した場合(一時停止ボタン等)の状態変化も
    反映される。Claudeが「今何が再生されているか」を確認するためのツール。
    """
    if is_leader:
        return _get_status()
    try:
        with urllib.request.urlopen(LEADER_BASE_URL + "/state", timeout=5) as resp:
            return dict(json.loads(resp.read()))
    except Exception as e:  # noqa: BLE001 - リーダー未応答等をユーザー向けに要約する
        return {"error": f"リーダープロセスへの問い合わせに失敗しました: {e}"}


@mcp.tool()
def render_choices(options: list[dict]) -> str:
    """複数の候補をサムネイル付きでブラウザに並べて表示し、ユーザーにクリックで選ばせる。

    再生する前に「これでいいか」をユーザーに確認させたい場合に使う。
    optionsの各要素は以下のキーを持つdict:
    - thumbnail_path (必須): 代表フレーム画像のUNCパス
    - label (必須): 候補の見出し(例: シーンの説明)
    - source_value (必須): 選択された場合に再生する動画のUNCパス
    - tag (省略可): 補足説明
    - seek_seconds (省略可): 選択時にその秒数から再生を開始する
    - channel (省略可): チャンネル番号

    呼び出す度に新しいタブでブラウザに一覧を表示する。ユーザーが選んだ結果は
    get_selection()で取得できる(まだ選ばれていなければ{"selected": None})。
    選択されたら、その内容をそのままplay_channelに渡して再生を開始する想定。
    """
    if is_leader:
        return _apply_render_choices(options)
    return _forward("/internal/render_choices", {"options": options})


@mcp.tool()
def get_selection() -> dict:
    """render_choicesで表示した候補のうち、ユーザーがクリックしたものを返す。

    まだ選択されていない場合は{"selected": None}を返す。選択済みなら
    {"selected": {"index":..., "thumbnail_path":..., "label":..., "source_value":...,
    "tag":..., "seek_seconds":..., "channel":...}}を返す。source_value/tag/
    seek_seconds/channelはそのままplay_channelの引数に渡せる。
    """
    if is_leader:
        return _get_selection()
    try:
        with urllib.request.urlopen(LEADER_BASE_URL + "/choices", timeout=5) as resp:
            data = dict(json.loads(resp.read()))
    except Exception as e:  # noqa: BLE001 - リーダー未応答等をユーザー向けに要約する
        return {"error": f"リーダープロセスへの問い合わせに失敗しました: {e}"}
    idx = data.get("selected_index")
    if idx is None:
        return {"selected": None}
    opt = dict(data["options"][idx])
    opt.pop("thumbnail_uri", None)
    return {"selected": {"index": idx, **opt}}


@mcp.tool()
def render_picture(path: str) -> str:
    """指定パス(UNC)の画像を既定ブラウザで表示する。

    直近5秒以内にタブがポーリングで生存確認できていれば同じタブの画像を
    差し替え、そうでなければ(未起動・タブを閉じた等)新規タブを開く
    (play_channelと同じタブ生存判定。4.4節)。
    """
    if is_leader:
        return _apply_render_picture(path)
    return _forward("/internal/render_picture", {"path": path})


if __name__ == "__main__":
    mcp.run()
