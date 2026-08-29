"""Windows側でネイティブに動く、picture/movie再生専用のrendererのMCPサーバー。

WSLには一切依存しない。Claudeがai-nas-manager(WSL側)から取得したメディアの場所
(UNCパス)を渡すと、既定ブラウザでプレイヤーページを表示・制御する。

設計ドキュメント: ai-nas-manager/docs/media-renderer-design.md
"""

from __future__ import annotations

import html
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
    "seek_to": None,
}
_last_seen: float | None = None


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
</style>
</head>
<body>
  <div id="overlay">&#9654; クリックして開始</div>
  <video id="player" controls></video>
  <div id="title"></div>
  <div id="tag"></div>
<script>
  const STATE_URL = {state_url};
  const video = document.getElementById('player');
  const overlay = document.getElementById('overlay');
  const titleEl = document.getElementById('title');
  const tagEl = document.getElementById('tag');
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

  function applyState(data) {{
    titleEl.textContent = data.title || '';
    tagEl.textContent = data.tag || '';
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


def _apply_play(
    source_type: str,
    source_value: str,
    channel: int | None,
    title: str | None,
    tag: str | None = None,
    seek_seconds: float | None = None,
) -> str:
    if source_type != "file":
        return f"未対応のsource_type: '{source_type}' (v-01は'file'のみ対応)"

    need_new_tab = (
        _last_seen is None or (time.time() - _last_seen) > TAB_ALIVE_TIMEOUT_SEC
    )
    file_uri = _unc_to_file_uri(source_value)
    with _state_lock:
        _state["source_type"] = source_type
        _state["source_value"] = file_uri
        _state["channel"] = channel
        _state["title"] = title
        _state["tag"] = tag
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
        global _last_seen
        if self.path == "/state":
            _last_seen = time.time()
            with _state_lock:
                self._send_json(dict(_state))
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
    """
    if is_leader:
        return _apply_play(source_type, source_value, channel, title, tag, seek_seconds)
    return _forward(
        "/internal/play",
        {
            "source_type": source_type,
            "source_value": source_value,
            "channel": channel,
            "title": title,
            "tag": tag,
            "seek_seconds": seek_seconds,
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
def render_picture(path: str) -> str:
    """指定パス(UNC)の画像を既定ブラウザで表示する。呼び出す度に新しいタブで開く。"""
    file_uri = _unc_to_file_uri(path)
    html_content = f"""<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>picture</title>
<style>
  html, body {{ margin:0; height:100%; background:#111; }}
  body {{ display:flex; align-items:center; justify-content:center; }}
  img {{ max-width:100vw; max-height:100vh; object-fit:contain; }}
</style>
</head>
<body><img src="{html.escape(file_uri, quote=True)}"></body>
</html>"""
    tmp_dir = Path(tempfile.gettempdir()) / "media-renderer"
    tmp_dir.mkdir(exist_ok=True)
    out_path = tmp_dir / "picture.html"
    out_path.write_text(html_content, encoding="utf-8")
    webbrowser.open(out_path.as_uri())
    return f"画像を表示しました: {path}"


if __name__ == "__main__":
    mcp.run()
