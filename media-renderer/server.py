"""Windows側でネイティブに動く、picture/movie再生専用のrendererのMCPサーバー。

WSLには一切依存しない。Claudeがai-nas-manager(WSL側)から取得したメディアの場所
(UNCパス)を渡すと、既定ブラウザでプレイヤーページを表示・制御する。

設計ドキュメント: ai-nas-manager/docs/media-renderer-design.md
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("media-renderer")

HTTP_HOST = "127.0.0.1"
HTTP_PORT = int(os.environ.get("MEDIA_RENDERER_HTTP_PORT", "39231"))
LEADER_BASE_URL = f"http://{HTTP_HOST}:{HTTP_PORT}"
NAS_EVENT_API_BASE = os.environ.get(
    "AI_NAS_EVENT_API_URL", "http://127.0.0.1:39232"
).rstrip("/")
NAS_EVENT_API_TOKEN = os.environ.get("AI_NAS_EVENT_TOKEN")

# プロセス起動ごとに一意なID。ブラウザ側のポーリングは通常seqの変化だけを見るが、
# 「サーバープロセスが再起動して内部のseqが0から数え直された結果、ブラウザが
# 保持している古いseqの値とたまたま一致し、変化なしと誤判定して再描画をスキップ
# する」という取り違えが実際に起きた(2026-08-31)。INSTANCE_IDが変わっていれば
# seqの値に関係なく必ず再描画させることで、この種のプロセス取り違えを防ぐ。
INSTANCE_ID = uuid.uuid4().hex

# タブが「生きている」とみなす、/stateポーリング(1秒間隔)からの許容経過時間。
# 設計ドキュメント4.4節: ポーリング間隔の5倍を閾値にする。
TAB_ALIVE_TIMEOUT_SEC = 5.0

# 新規タブを開いた場合に、実際にページが読み込まれ最初のポーリングが届くまで
# 待つ上限(ブラウザ起動+ページ読み込みの時間を見込む)。既存タブ再利用の場合は
# 直近のポーリングで即座に確認できるはずなので、もっと短い上限で十分。
_NEW_TAB_ACK_TIMEOUT_SEC = 6.0
_REUSE_TAB_ACK_TIMEOUT_SEC = 2.0

# webbrowser.open()はOSの既定ブラウザに丸投げするため、既定ブラウザが
# 何になっているか(Edge/Chrome等)によって挙動が変わり、「表示したつもりの
# タブがユーザーの見ているブラウザと違う」という混乱の原因になった
# (2026-08-31, 既定ブラウザが把握しないままChromeに変わっていた)。
# 見つかった場合はEdgeを名指しで起動し、常に同じブラウザに開く。
_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _open_in_browser(file_uri: str) -> None:
    for candidate in _EDGE_CANDIDATES:
        if Path(candidate).exists():
            subprocess.Popen([candidate, file_uri])
            return
    webbrowser.open(file_uri)  # Edgeが見つからない場合のみOS既定ブラウザにフォールバック


def _wait_for_ack(get_last_seen: Callable[[], float | None], since: float, timeout: float) -> bool:
    """呼び出し時刻(since)より後に、対応するポーリングエンドポイントへのGETが
    実際に届いたかを確認する。届いていなければFalseを返し、呼び出し元は
    「表示できたつもりで実は届いていない」という結果を返さずに済む。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        seen = get_last_seen()
        if seen is not None and seen >= since:
            return True
        time.sleep(0.15)
    return False

# どのページ(player/chooser/picture)にも共通で埋め込む、接続状態の可視化オーバーレイ。
# 「クリックしても反応しない」系の不具合をユーザー自身が画面を見ただけで気づける
# ようにする(diagnose_connectionをClaudeが呼ぶまでもなく、画面の右上を見れば
# 直近のポーリングが成功しているか・リーダーのinstance_idが変わっていないかが
# 分かる)。2026-08-31、ユーザー指摘により追加。
DEBUG_OVERLAY_CSS = """
  #debug {
    position:fixed; top:8px; right:8px; width:200px; height:40px; z-index:999;
    font-family:monospace; font-size:11px; color:#7CFC7C;
    background:rgba(0,0,0,0.65); padding:4px 8px; border-radius:4px;
    box-sizing:border-box; white-space:pre; pointer-events:none; text-align:right;
  }
  #debug.stale { color:#ff6b6b; }
"""

DEBUG_OVERLAY_JS = """
  let lastPollOkAt = null;
  function mrDebug(ok, data) {
    const now = new Date().toLocaleTimeString('ja-JP', {hour12:false});
    if (ok) {
      lastPollOkAt = now;
      debugEl.classList.remove('stale');
      debugEl.textContent = 'OK ' + now
        + '\\nseq=' + (data ? data.seq : '?')
        + ' id=' + (data && data.instance_id ? data.instance_id.slice(0, 8) : '?');
    } else {
      debugEl.classList.add('stale');
      debugEl.textContent = 'ERR 応答なし\\n最終成功: ' + (lastPollOkAt || 'なし');
    }
  }
"""


def _nas_event_bridge_js(view_kind: str) -> str:
    """Viewとai-nas-managerイベントAPIを直接接続する共通JavaScript。"""
    template = r"""
  const NAS_EVENT_API_BASE = __BASE__;
  const NAS_EVENT_TOKEN = __TOKEN__;
  const NAS_VIEW_KIND = __VIEW_KIND__;
  const NAS_EVENT_HEADERS = {'Content-Type': 'application/json'};
  if (NAS_EVENT_TOKEN) {
    NAS_EVENT_HEADERS['Authorization'] = 'Bearer ' + NAS_EVENT_TOKEN;
  }

  let nasViewId;
  let nasCursor = 0;
  try {
    nasViewId = localStorage.getItem('aiNasViewId');
    if (!nasViewId) {
      nasViewId = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
      localStorage.setItem('aiNasViewId', nasViewId);
    }
    nasCursor = Number(localStorage.getItem('aiNasCursor:' + NAS_VIEW_KIND) || '0');
  } catch (e) {
    nasViewId = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }
  const nasConsumer = 'windows-view:' + NAS_VIEW_KIND + ':' + nasViewId;

  function nasDebug(ok, detail) {
    let el = document.getElementById('nas-debug');
    if (!el) {
      el = document.createElement('div');
      el.id = 'nas-debug';
      Object.assign(el.style, {
        position:'fixed', top:'52px', right:'8px', width:'200px', height:'22px',
        zIndex:'999', fontFamily:'monospace', fontSize:'11px',
        background:'rgba(0,0,0,0.65)', padding:'4px 8px',
        borderRadius:'4px', boxSizing:'border-box', pointerEvents:'none',
        textAlign:'right'
      });
      document.body.appendChild(el);
    }
    el.style.color = ok ? '#7CFC7C' : '#ff6b6b';
    el.textContent = ok ? ('NAS OK ' + detail) : ('NAS ERR ' + detail);
  }

  async function postNasEvent(eventType, payload, dedupeKey) {
    try {
      const response = await fetch(NAS_EVENT_API_BASE + '/events', {
        method: 'POST',
        headers: NAS_EVENT_HEADERS,
        body: JSON.stringify({
          source: 'windows-view',
          target: 'nas',
          event_type: eventType,
          payload: payload || {},
          dedupe_key: dedupeKey || null
        })
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      nasDebug(true, 'sent=' + data.event.id);
      return data.event;
    } catch (error) {
      nasDebug(false, 'send');
      return null;
    }
  }

  async function ackNasEvents() {
    await fetch(NAS_EVENT_API_BASE + '/acks', {
      method: 'POST',
      headers: NAS_EVENT_HEADERS,
      body: JSON.stringify({consumer: nasConsumer, last_event_id: nasCursor})
    });
  }

  async function pollNasEvents() {
    try {
      const url = NAS_EVENT_API_BASE + '/events?target=view&after_id='
        + nasCursor + '&limit=100';
      const response = await fetch(url, {cache:'no-store', headers:NAS_EVENT_HEADERS});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      for (const event of (data.events || [])) {
        if (typeof handleNasEvent === 'function') {
          await handleNasEvent(event);
        }
        nasCursor = Math.max(nasCursor, Number(event.id));
      }
      try {
        localStorage.setItem('aiNasCursor:' + NAS_VIEW_KIND, String(nasCursor));
      } catch (e) {}
      if ((data.events || []).length) await ackNasEvents();
      nasDebug(true, 'recv=' + nasCursor);
    } catch (error) {
      nasDebug(false, 'poll');
    }
  }

  postNasEvent('view_connected', {
    view: NAS_VIEW_KIND,
    consumer: nasConsumer,
    user_agent: navigator.userAgent
  });
  setInterval(pollNasEvents, 750);
  pollNasEvents();
"""
    return (
        template.replace("__BASE__", json.dumps(NAS_EVENT_API_BASE))
        .replace("__TOKEN__", json.dumps(NAS_EVENT_API_TOKEN))
        .replace("__VIEW_KIND__", json.dumps(view_kind))
    )


_PLAYER_DIR = Path(tempfile.gettempdir()) / "media-renderer"
_PLAYER_HTML_PATH = _PLAYER_DIR / "player.html"
_CHOICES_HTML_PATH = _PLAYER_DIR / "chooser.html"
_PICTURE_HTML_PATH = _PLAYER_DIR / "picture.html"
_VIEW_HTML_PATH = _PLAYER_DIR / "view.html"

# Claude CodeとClaude Desktopなど、同じMCPサーバー定義から複数プロセスが同時に
# 起動されることがある(windows-message-mcpで経験済みの問題)。固定ポートを
# 取得できたプロセスだけが「リーダー」として実際の再生状態を保持し、
# 取得できなかった「フォロワー」はツール呼び出しをリーダーへHTTP転送する。
is_leader = False

_LEADER_RETRY_SEC = 1.0
_leadership_lock = threading.Lock()
_control_server: ThreadingHTTPServer | None = None
_control_thread: threading.Thread | None = None


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

_view_lock = threading.Lock()
_view_state: dict[str, Any] = {
    "mode": "player",
    "seq": 0,
}
_view_last_seen: float | None = None


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
{DEBUG_OVERLAY_CSS}
</style>
</head>
<body>
  <div id="overlay">&#9654; クリックして開始</div>
  <video id="player" controls></video>
  <div id="title"></div>
  <div id="tag"></div>
  <img id="thumbnail" alt="thumbnail">
  <div id="debug"></div>
<script>
  const STATE_URL = {state_url};
  const video = document.getElementById('player');
  const overlay = document.getElementById('overlay');
  const titleEl = document.getElementById('title');
  const tagEl = document.getElementById('tag');
  const thumbEl = document.getElementById('thumbnail');
  const debugEl = document.getElementById('debug');
  let started = false;
  let lastSeq = -1;
  let lastInstanceId = null;
  let currentState = null;
{DEBUG_OVERLAY_JS}
{_nas_event_bridge_js("player")}

  function reportBrowserState(command) {{
    fetch(STATE_URL, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{command}})
    }}).catch(() => {{}});
    postNasEvent('playback_state', {{
      command,
      channel: currentState ? currentState.channel : null,
      title: currentState ? currentState.title : null,
      current_time: Number.isFinite(video.currentTime) ? video.currentTime : null
    }});
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

  video.addEventListener('play', () => {{
    if (video.dataset.ignorePause === '1') return;
    reportBrowserState('play');
  }});

  video.addEventListener('pause', () => {{
    if (video.dataset.ignorePause === '1') return;
    reportBrowserState('stop');
  }});

  async function handleNasEvent(event) {{
    if (event.event_type === 'status_request') {{
      await postNasEvent('view_status', {{
        request_event_id: event.id,
        view: 'player',
        started,
        command: currentState ? currentState.command : null,
        channel: currentState ? currentState.channel : null,
        title: currentState ? currentState.title : null,
        current_time: Number.isFinite(video.currentTime) ? video.currentTime : null
      }});
      return;
    }}
    if (event.event_type !== 'playback_command') return;
    const payload = event.payload || {{}};
    const command = payload.command;
    if (command === 'seek') {{
      const seconds = Number(payload.seconds);
      if (Number.isFinite(seconds)) applySeek(seconds);
      return;
    }}
    if (command === 'stop') {{
      currentState = {{...(currentState || {{}}), command:'stop'}};
      if (started) applyState(currentState);
      return;
    }}
    if (command === 'play') {{
      currentState = {{
        ...(currentState || {{}}),
        command: 'play',
        source_type: payload.source_type || 'file',
        source_value: payload.source_uri || payload.source_value,
        title: payload.title || '',
        tag: payload.tag || '',
        thumbnail: payload.thumbnail_uri || null,
        seek_to: payload.seek_seconds
      }};
      titleEl.textContent = currentState.title;
      tagEl.textContent = currentState.tag;
      applyThumbnail(currentState);
      if (started) applyState(currentState);
    }}
  }}

  async function poll() {{
    try {{
      const res = await fetch(STATE_URL, {{cache: 'no-store'}});
      const data = await res.json();
      mrDebug(true, data);
      // instance_idが変わっていれば、リーダープロセスが再起動して内部の
      // seqが0から数え直されている可能性がある。seqの一致・不一致に関係なく
      // 必ず反映する(そうしないと、たまたま同じseqの値になった場合に
      // 「変化なし」と誤判定して再生指示を取りこぼす)。
      const instanceChanged = data.instance_id !== lastInstanceId;
      const rendererChanged = instanceChanged || data.seq !== lastSeq;
      if (rendererChanged) {{
        currentState = data;
        lastSeq = data.seq;
        lastInstanceId = data.instance_id;
        titleEl.textContent = data.title || '';
        tagEl.textContent = data.tag || '';
        applyThumbnail(data);
        if (started) applyState(data);
      }} else {{
        lastInstanceId = data.instance_id;
      }}
    }} catch (e) {{
      mrDebug(false);
      /* リーダー未応答。次回ポーリングで再試行する */
    }}
  }}

  setInterval(poll, 1000);
  poll();
</script>
</body>
</html>"""


def _open_player_page() -> None:
    _open_unified_view()


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
{DEBUG_OVERLAY_CSS}
</style>
</head>
<body>
  <div id="empty">候補が指定されていません。</div>
  <div id="grid"></div>
  <div id="status"></div>
  <div id="debug"></div>
<script>
  const CHOICES_URL = {choices_url};
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  const status = document.getElementById('status');
  const debugEl = document.getElementById('debug');
  let lastSeq = -1;
  let lastInstanceId = null;
  let currentOptions = [];
{DEBUG_OVERLAY_JS}
{_nas_event_bridge_js("chooser")}

  function render(data) {{
    const options = data.options || [];
    currentOptions = options;
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
    const option = currentOptions[index] || null;
    postNasEvent('selection', {{
      index,
      option,
      label: option ? option.label : null,
      source_value: option ? option.source_value : null
    }});
  }}

  async function poll() {{
    try {{
      const res = await fetch(CHOICES_URL, {{cache: 'no-store'}});
      const data = await res.json();
      mrDebug(true, data);
      const instanceChanged = data.instance_id !== lastInstanceId;
      if (instanceChanged || data.seq !== lastSeq) {{
        lastSeq = data.seq;
        lastInstanceId = data.instance_id;
        render(data);
      }} else {{
        lastInstanceId = data.instance_id;
      }}
    }} catch (e) {{
      mrDebug(false);
      /* リーダー未応答。次回ポーリングで再試行する */
    }}
  }}

  setInterval(poll, 800);
  poll();
</script>
</body>
</html>"""


def _open_choices_page() -> None:
    _open_unified_view()


def _picture_html() -> str:
    picture_url = json.dumps(LEADER_BASE_URL + "/picture")
    return f"""<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>picture</title>
<style>
  html, body {{ margin:0; height:100%; background:#111; }}
  body {{ display:flex; align-items:center; justify-content:center; }}
  img {{ max-width:100vw; max-height:100vh; object-fit:contain; display:none; }}
{DEBUG_OVERLAY_CSS}
</style>
</head>
<body>
<img id="picture" alt="picture">
<div id="debug"></div>
<script>
  const PICTURE_URL = {picture_url};
  const img = document.getElementById('picture');
  const debugEl = document.getElementById('debug');
  let lastSeq = -1;
  let lastInstanceId = null;
{DEBUG_OVERLAY_JS}

{_nas_event_bridge_js("picture")}
  async function poll() {{
    try {{
      const res = await fetch(PICTURE_URL, {{cache: 'no-store'}});
      const data = await res.json();
      mrDebug(true, data);
      const instanceChanged = data.instance_id !== lastInstanceId;
      lastInstanceId = data.instance_id;
      if (instanceChanged || data.seq !== lastSeq) {{
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
      mrDebug(false);
      /* リーダー未応答。次回ポーリングで再試行する */
    }}
  }}

  setInterval(poll, 1000);
  poll();
</script>
</body>
</html>"""


def _unified_view_html() -> str:
    view_url = json.dumps(LEADER_BASE_URL + "/view")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>AI NAS Manager View</title>
<style>
  html, body {{ margin:0; width:100%; height:100%; overflow:hidden; background:#000; }}
  iframe {{
    position:fixed; inset:0; width:100%; height:100%; border:0;
    display:none; background:#000;
  }}
  iframe.active {{ display:block; }}
</style>
</head>
<body>
  <iframe id="view-player" src="player.html" title="player"></iframe>
  <iframe id="view-chooser" src="chooser.html" title="chooser"></iframe>
  <iframe id="view-picture" src="picture.html" title="picture"></iframe>
<script>
  const VIEW_URL = {view_url};
  const frames = {{
    player: document.getElementById('view-player'),
    chooser: document.getElementById('view-chooser'),
    picture: document.getElementById('view-picture')
  }};
  let activeMode = null;

  function show(mode) {{
    const nextMode = Object.prototype.hasOwnProperty.call(frames, mode)
      ? mode : 'player';
    if (nextMode === activeMode) return;
    Object.entries(frames).forEach(([name, frame]) => {{
      frame.classList.toggle('active', name === nextMode);
    }});
    activeMode = nextMode;
  }}

  async function poll() {{
    try {{
      const response = await fetch(VIEW_URL, {{cache:'no-store'}});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      show(data.mode);
    }} catch (error) {{
      /* 次のポーリングで再試行する */
    }}
  }}

  show('player');
  setInterval(poll, 500);
  poll();
</script>
</body>
</html>"""


def _set_active_view(mode: str) -> None:
    with _view_lock:
        _view_state["mode"] = mode
        _view_state["seq"] += 1


def _open_unified_view() -> None:
    """Generate every child page and open their single top-level container."""
    _PLAYER_DIR.mkdir(exist_ok=True)
    _PLAYER_HTML_PATH.write_text(_player_html(), encoding="utf-8")
    _CHOICES_HTML_PATH.write_text(_choices_html(), encoding="utf-8")
    _PICTURE_HTML_PATH.write_text(_picture_html(), encoding="utf-8")
    _VIEW_HTML_PATH.write_text(_unified_view_html(), encoding="utf-8")
    _open_in_browser(_VIEW_HTML_PATH.as_uri())


def _open_picture_page() -> None:
    _open_unified_view()


def _apply_render_picture(path: str) -> str:
    call_time = time.time()
    file_uri = _unc_to_file_uri(path)
    need_new_tab = (
        _view_last_seen is None
        or (call_time - _view_last_seen) > TAB_ALIVE_TIMEOUT_SEC
    )
    with _picture_lock:
        _picture_state["picture_uri"] = file_uri
        _picture_state["seq"] += 1
    _set_active_view("picture")
    if need_new_tab:
        _open_picture_page()
        if not _wait_for_ack(lambda: _picture_last_seen, call_time, _NEW_TAB_ACK_TIMEOUT_SEC):
            return (
                "画像をブラウザで開こうとしましたが、応答が確認できませんでした。"
                f"手動で確認してください(ファイル: {_VIEW_HTML_PATH})。"
            )
        return f"画像を表示しました: {path}"
    if not _wait_for_ack(lambda: _picture_last_seen, call_time, _REUSE_TAB_ACK_TIMEOUT_SEC):
        return (
            "画像の切り替えを試みましたが、既存のタブからの応答が確認できませんでした。"
            "タブが閉じられているか、固まっている可能性があります。"
        )
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

    call_time = time.time()
    need_new_tab = (
        _view_last_seen is None or (call_time - _view_last_seen) > TAB_ALIVE_TIMEOUT_SEC
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
    _set_active_view("player")

    label = title or (f"CH{channel}" if channel is not None else source_value)
    seek_note = f"({seek_seconds:.1f}秒の位置から)" if seek_seconds is not None else ""
    if need_new_tab:
        _open_player_page()
        if not _wait_for_ack(lambda: _last_seen, call_time, _NEW_TAB_ACK_TIMEOUT_SEC):
            return (
                "プレイヤーをブラウザで開こうとしましたが、応答が確認できませんでした。"
                f"手動で確認してください(ファイル: {_VIEW_HTML_PATH})。"
            )
        return (
            f"プレイヤーをブラウザで開き、{label}{seek_note}の再生を指示しました。"
            "初回のみ画面の「クリックして開始」を押してください。"
        )
    if not _wait_for_ack(lambda: _last_seen, call_time, _REUSE_TAB_ACK_TIMEOUT_SEC):
        return (
            f"{label}{seek_note}への切り替えを試みましたが、"
            "既存のタブからの応答が確認できませんでした。"
            "タブが閉じられているか、固まっている可能性があります。"
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
    call_time = time.time()
    need_new_tab = (
        _view_last_seen is None
        or (call_time - _view_last_seen) > TAB_ALIVE_TIMEOUT_SEC
    )
    with _choice_lock:
        _choice_state["options"] = processed
        _choice_state["selected_index"] = None
        _choice_state["seq"] += 1
    _set_active_view("chooser")
    if need_new_tab:
        _open_choices_page()
        if not _wait_for_ack(lambda: _choice_last_seen, call_time, _NEW_TAB_ACK_TIMEOUT_SEC):
            return (
                f"{len(processed)}件の候補を表示しようとしましたが、応答が確認できませんでした。"
                f"手動で確認してください(ファイル: {_VIEW_HTML_PATH})。"
            )
        return f"{len(processed)}件の候補をブラウザに表示しました。選択されたらget_selectionで取得できます。"
    if not _wait_for_ack(lambda: _choice_last_seen, call_time, _REUSE_TAB_ACK_TIMEOUT_SEC):
        return (
            f"{len(processed)}件の候補への更新を試みましたが、"
            "既存のタブからの応答が確認できませんでした。"
            "タブが閉じられているか、固まっている可能性があります。"
        )
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
        # Private Network Access (PNA): Chromium系ブラウザは、file://等の
        # ページからプライベートアドレス(127.0.0.1含む)へfetchする際、
        # 事前にOPTIONSプリフライトを送りこのヘッダーでの許可を要求する
        # (2026-08-31、対応していなかったため実際のfetchが"Failed to fetch"で
        # サイレントに失敗する不具合を確認)。単純リクエストの応答にも念のため付与。
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return dict(json.loads(raw or b"{}"))

    def do_OPTIONS(self) -> None:  # noqa: N802 - PNA/CORSプリフライト応答
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandlerの命名規則
        global _last_seen, _choice_last_seen, _picture_last_seen, _view_last_seen
        if self.path == "/view":
            _view_last_seen = time.time()
            with _view_lock:
                self._send_json({**_view_state, "instance_id": INSTANCE_ID})
            return
        if self.path == "/state":
            _last_seen = time.time()
            with _state_lock:
                self._send_json({**_state, "instance_id": INSTANCE_ID})
            return
        if self.path == "/choices":
            _choice_last_seen = time.time()
            with _choice_lock:
                self._send_json({**_choice_state, "instance_id": INSTANCE_ID})
            return
        if self.path == "/picture":
            _picture_last_seen = time.time()
            with _picture_lock:
                self._send_json({**_picture_state, "instance_id": INSTANCE_ID})
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


def _try_become_leader() -> bool:
    """Start the control server if this process can claim the fixed port."""
    global is_leader, _control_server, _control_thread

    with _leadership_lock:
        if is_leader and _control_thread is not None and _control_thread.is_alive():
            return True

        if _control_server is not None:
            try:
                _control_server.server_close()
            except OSError:
                pass
        is_leader = False
        _control_server = None
        _control_thread = None

        try:
            httpd = _ExclusiveThreadingHTTPServer((HTTP_HOST, HTTP_PORT), _Handler)
        except OSError:
            return False

        def _serve() -> None:
            global is_leader, _control_server, _control_thread
            try:
                httpd.serve_forever()
            finally:
                with _leadership_lock:
                    if _control_server is httpd:
                        is_leader = False
                        _control_server = None
                        _control_thread = None
                httpd.server_close()

        thread = threading.Thread(
            target=_serve,
            daemon=True,
            name="media-renderer-control",
        )
        _control_server = httpd
        _control_thread = thread
        is_leader = True
        thread.start()
        return True


def _leadership_watchdog_step() -> bool:
    """Retry leader election when the leader or its HTTP thread is gone."""
    if is_leader and _control_thread is not None and _control_thread.is_alive():
        return True
    return _try_become_leader()


def _leadership_watchdog() -> None:
    while True:
        _leadership_watchdog_step()
        time.sleep(_LEADER_RETRY_SEC)


_try_become_leader()
threading.Thread(
    target=_leadership_watchdog,
    daemon=True,
    name="media-renderer-leader-watchdog",
).start()


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
    if _leadership_watchdog_step():
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
    if _leadership_watchdog_step():
        return _apply_stop()
    return _forward("/internal/stop", {})


@mcp.tool()
def seek(position_seconds: float) -> str:
    """再生中のソースを切り替えずに、指定秒数の位置にシークする。

    未再生の状態で呼んでも、再生指示が来た時点で反映される(状態としては
    保持される)。ソース自体を切り替えたい場合はplay_channelのseek_seconds
    引数を使うこと。
    """
    if _leadership_watchdog_step():
        return _apply_seek(position_seconds)
    return _forward("/internal/seek", {"seconds": position_seconds})


@mcp.tool()
def get_playback_status() -> dict:
    """現在の再生状態(ソース・チャンネル・タイトル・tag・再生中かどうか)を返す。

    ユーザーがブラウザ側で直接操作した場合(一時停止ボタン等)の状態変化も
    反映される。Claudeが「今何が再生されているか」を確認するためのツール。
    """
    if _leadership_watchdog_step():
        return _get_status()
    try:
        with urllib.request.urlopen(LEADER_BASE_URL + "/state", timeout=5) as resp:
            return dict(json.loads(resp.read()))
    except Exception as e:  # noqa: BLE001 - リーダー未応答等をユーザー向けに要約する
        return {"error": f"リーダープロセスへの問い合わせに失敗しました: {e}"}


@mcp.tool()
def diagnose_connection() -> dict:
    """media_rendererの接続状態を自己診断する。

    「タブをクリックしても反応しない」「表示したはずなのに反映されない」等の
    不具合を調べる最初の一歩として使う(design doc 4.1.2節の汎用チェックリストの
    1・2番を自動化したもの)。このプロセス自身がリーダーかどうか、リーダーの場合は
    実際にブラウザタブがポーリングしてきているか(state/choices/pictureそれぞれの
    最終ポーリングからの経過秒数)とそのリーダー固有のINSTANCE_IDを返す。
    フォロワーの場合はリーダーへの到達性を確認する。

    last_seen_ago_secがNone、またはTAB_ALIVE_TIMEOUT_SEC(5秒)を大きく超えている
    場合、対応するブラウザタブは開いていないか応答していない(サーバー側は
    正常でも、タブが閉じられている・固まっている・別プロセスに繋がっている等)。
    """
    now = time.time()

    def _ago(last_seen: float | None) -> float | None:
        return round(now - last_seen, 1) if last_seen is not None else None

    if _leadership_watchdog_step():
        return {
            "is_leader": True,
            "http_port": HTTP_PORT,
            "instance_id": INSTANCE_ID,
            "state_last_seen_ago_sec": _ago(_last_seen),
            "choices_last_seen_ago_sec": _ago(_choice_last_seen),
            "active_view": _view_state["mode"],
            "view_last_seen_ago_sec": _ago(_view_last_seen),
            "picture_last_seen_ago_sec": _ago(_picture_last_seen),
        }
    try:
        with urllib.request.urlopen(LEADER_BASE_URL + "/state", timeout=5) as resp:
            leader_state = dict(json.loads(resp.read()))
        return {
            "is_leader": False,
            "http_port": HTTP_PORT,
            "leader_reachable": True,
            "leader_instance_id": leader_state.get("instance_id"),
        }
    except Exception as e:  # noqa: BLE001 - リーダー未応答等をユーザー向けに要約する
        return {
            "is_leader": False,
            "http_port": HTTP_PORT,
            "leader_reachable": False,
            "error": f"リーダープロセスへの問い合わせに失敗しました: {e}",
        }


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

    単一の統合Viewを候補一覧へ切り替える。ユーザーが選んだ結果はget_selection()で
    取得できる(まだ選ばれていなければ{"selected": None})。
    選択されたら、その内容をそのままplay_channelに渡して再生を開始する想定。
    """
    if _leadership_watchdog_step():
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
    if _leadership_watchdog_step():
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

    単一の統合Viewを画像表示へ切り替える。統合Viewが閉じられている場合だけ
    新しいタブを開く。
    """
    if _leadership_watchdog_step():
        return _apply_render_picture(path)
    return _forward("/internal/render_picture", {"path": path})


if __name__ == "__main__":
    mcp.run()
