"""AI NAS manager MCPサーバーの骨組み。

将来的にRockchip NAS上で動かし、メディア管理(整理・分類など)を
自然言語で操作できるようにするための土台。
v-01ではTuner(仮想Tuner)発見・状態取得・番組表取得のMCP連携パターンを追加した。
"""

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import anyio

import event_api
import event_queue
from mcp.server.mcpserver import MCPServer

import media_catalog
import media_index
import tuner_client as _tuner_client
import video_fragmentation
from discovery import TunerInfo, discover_tuners as _discover_tuners

mcp = MCPServer("ai-nas-manager")
event_api.start_event_api()


THUMBNAILS_DIR = Path(__file__).resolve().parent / "media" / "thumbnails"


def _generate_fragment_thumbnail(video_path: Path, stem: str, index: int, at_seconds: float) -> str | None:
    """fragmentの代表フレームを永続サムネイルとして保存し、パスを返す。

    映像ファイルが存在しない、あるいはffmpegでの抽出に失敗した場合はNoneを返し、
    register_media自体は失敗させない(サムネイルはあくまで付加情報のため)。
    """
    if not video_path.exists():
        return None
    out_path = THUMBNAILS_DIR / f"{stem}_f{index}.png"
    try:
        video_fragmentation.extract_frame(video_path, at_seconds, out_path)
    except Exception:  # noqa: BLE001 - サムネイル生成はベストエフォート
        return None
    return str(out_path)


@mcp.tool()
def ping() -> str:
    """疎通確認用のダミーツール。"""
    return "pong"


@mcp.tool()
def receive_view_events(
    after_id: int | None = None,
    limit: int = 100,
    consumer: str = "ai-nas-manager",
    acknowledge: bool = False,
) -> dict:
    """Windows ViewからNAS宛てに届いたイベントを連番順に取得する。

    after_idを省略するとconsumerのACK済み位置から取得する。acknowledge=Trueなら、
    返した最後のイベントまで同時にACKする。イベントはSQLiteに永続化されるため、
    MCPやViewの再起動後も未処理分を取得できる。
    """
    cursor = event_queue.get_cursor(consumer) if after_id is None else after_id
    events = event_queue.list_events("nas", after_id=cursor, limit=limit)
    last_event_id = events[-1].id if events else cursor
    if acknowledge and events:
        event_queue.acknowledge(consumer, last_event_id)
    return {
        "consumer": consumer,
        "after_id": cursor,
        "last_event_id": last_event_id,
        "events": [event.as_dict() for event in events],
    }


@mcp.tool()
def acknowledge_view_events(
    last_event_id: int,
    consumer: str = "ai-nas-manager",
) -> dict:
    """Viewイベントを指定IDまで処理済みにする。ACK位置は後退しない。"""
    cursor = event_queue.acknowledge(consumer, last_event_id)
    return {"consumer": consumer, "last_event_id": cursor}


@mcp.tool()
def publish_view_event(
    event_type: str,
    payload: dict | None = None,
    dedupe_key: str | None = None,
) -> dict:
    """AI NAS ManagerからWindows View宛てのイベントを永続キューへ発行する。

    Viewはevent_type='playback_command'を解釈する。payload.commandには
    'play'/'stop'/'seek'を指定でき、playはsource_valueまたはsource_uri、
    seekはsecondsを使用する。
    """
    normalized_payload = dict(payload or {})
    if (
        event_type == "playback_command"
        and normalized_payload.get("command") == "play"
    ):
        distro = os.environ.get("AI_NAS_WSL_DISTRO", "Ubuntu")
        for source_key, uri_key in (
            ("source_value", "source_uri"),
            ("thumbnail_path", "thumbnail_uri"),
        ):
            value = normalized_payload.get(source_key)
            if value and uri_key not in normalized_payload and str(value).startswith("/"):
                encoded = quote(str(value), safe="/:")
                normalized_payload[uri_key] = f"file://wsl.localhost/{distro}{encoded}"

    event = event_queue.publish(
        source="ai-nas-manager",
        target="view",
        event_type=event_type,
        payload=normalized_payload,
        dedupe_key=dedupe_key,
    )
    return event.as_dict()


@mcp.tool()
def get_view_event_status(consumer: str = "ai-nas-manager") -> dict:
    """直接イベントAPIとキューの現在位置を返す。"""
    return {
        "event_api": {
            "host": event_api.EVENT_API_HOST,
            "port": event_api.EVENT_API_PORT,
            "is_leader": event_api.is_leader,
            "instance_id": event_api.INSTANCE_ID,
        },
        "nas_latest_event_id": event_queue.latest_event_id("nas"),
        "view_latest_event_id": event_queue.latest_event_id("view"),
        "consumer": consumer,
        "consumer_last_event_id": event_queue.get_cursor(consumer),
    }

@mcp.tool()
def list_media(path: str = ".") -> str:
    """指定パス配下のメディアファイル一覧を返す(未実装のダミー応答)。"""
    return f"TODO: 未実装。{path} 配下のメディア一覧をここに実装する。"


@mcp.tool()
def list_media_channels() -> list[dict]:
    """仮想メディアチャンネル(CH1〜CH4)の一覧を返す。"""
    return [
        {"channel": c.channel, "title": c.title, "tag": c.tag}
        for c in media_catalog.list_channels()
    ]


@mcp.tool()
def get_media_location(channel: int) -> dict:
    """指定したチャンネルのメディアの場所を返す。存在しないチャンネル番号はエラー。

    戻り値のsourceは{"type": "file", "path": <WSL絶対パス>}の形。
    Windows側のmedia_rendererへ渡す際はUNCパスへの変換が必要
    (ai-nas-manager/docs/media-renderer-design.md 3.3節・6節)。
    tagは、映像を意味理解して生成した内容の説明文
    (ai-nas-manager/docs/semantic-tagging-experiment.md 6節)。
    チャンネル選択時にユーザーへ提示することを想定している。
    """
    c = media_catalog.get_channel(channel)
    return {
        "channel": c.channel,
        "title": c.title,
        "tag": c.tag,
        "source": {"type": "file", "path": str(c.path)},
    }


@mcp.tool()
def search_media(keyword: str) -> list[dict]:
    """タイトル・tag・fragmentの説明文にkeywordを含むメディアを検索する。

    CH1〜4(media_catalog、決め打ち)と、register_mediaで登録されたライブラリの
    両方が対象。originフィールドで"channel"/"library"を区別する。channel由来は
    get_fragment_details(channel)でfragment内訳を追加取得できる。library由来は
    fragmentsをこの結果に直接含める。

    ai-nas-manager/docs/semantic-tagging-experiment.md 9節のNASインデックス構想の
    最小実装。将来的には大規模なライブラリに対する高速検索(SQLite等)に
    置き換わる想定だが、呼び出し側から見たインターフェースはそのまま使える
    よう設計している。
    """
    channel_results = [
        {"origin": "channel", "channel": c.channel, "title": c.title, "tag": c.tag}
        for c in media_catalog.search_channels(keyword)
    ]
    library_results = [
        {
            "origin": "library",
            "path": e.path,
            "title": e.title,
            "tag": e.tag,
            "fragments": [
                {
                    "start": f.start,
                    "end": f.end,
                    "description": f.description,
                    "thumbnail_path": f.thumbnail_path,
                }
                for f in e.fragments
            ],
        }
        for e in media_index.search(keyword)
    ]
    return channel_results + library_results


@mcp.tool()
def register_media(path: str, title: str, tag: str, fragments: list[dict]) -> dict:
    """任意の映像をライブラリインデックスに登録する(同じpathがあれば上書き)。

    段階1(video_fragmentation.fragment_video/analyze_video)と、それに続く
    Claude自身による言語化・統合の結果をここに書き込む。fragmentsは
    [{"start": float, "end": float, "description": str}, ...]の形。
    一度登録すればsearch_mediaで検索でき、list_pending_mediaの対象からも外れる。
    Claudeが自律的に「発見→解析→登録」の一連を行うための書き込み口。

    各fragmentの代表フレーム(区間の中間時点)を自動でサムネイルとして
    ai-nas-manager/media/thumbnails/に永続保存し、thumbnail_pathとして
    fragmentに含める(search_mediaの結果にもそのまま乗る)。start(区間境界)
    ちょうどはシーン変化の瞬間(≒ブレやフレームアウト)であることが多く
    代表フレームとして不適切なため、中間時点を使う。映像ファイルが存在しない、
    または抽出に失敗した場合はthumbnail_path=Noneのまま登録を続ける。
    """
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    video_path = Path(path)
    enriched_fragments = [
        {
            **f,
            "thumbnail_path": _generate_fragment_thumbnail(
                video_path, video_path.stem, i, (f["start"] + f["end"]) / 2
            ),
        }
        for i, f in enumerate(fragments)
    ]

    entry = media_index.register(path, title, tag, enriched_fragments)
    return {
        "path": entry.path,
        "title": entry.title,
        "tag": entry.tag,
        "fragments": [
            {
                "start": f.start,
                "end": f.end,
                "description": f.description,
                "thumbnail_path": f.thumbnail_path,
            }
            for f in entry.fragments
        ],
    }


@mcp.tool()
def list_pending_media(scan_dir: str) -> list[str]:
    """指定ディレクトリ(WSL絶対パス)直下で、まだregister_mediaされていない
    映像ファイルの一覧を返す。

    Claudeが自律的に「何を処理すべきか」を自分で見つけるための入り口。
    見つけたパスはanalyze_video→(Claude自身による言語化・統合)→register_media
    という流れで処理する想定(semantic-tagging-experiment.md 11節)。
    media_catalog(CH1〜4)に既に登録済みのパスも「処理済み」として除外する。
    """
    catalog_paths = {str(c.path) for c in media_catalog.list_channels()}
    return [p for p in media_index.list_pending(scan_dir) if p not in catalog_paths]


@mcp.tool()
def get_fragment_details(channel: int) -> dict:
    """指定チャンネルのfragment単位の内訳(時間区間+説明文)を返す。

    映像全体のtagだけでは薄まってしまう、短時間だけ映る内容を検索・特定する
    ために使う(semantic-tagging-experiment.md 9節)。返るstartの秒数は、
    media_renderer.play_channelのseek_secondsにそのまま渡せる。
    """
    c = media_catalog.get_channel(channel)
    return {
        "channel": c.channel,
        "title": c.title,
        "fragments": [
            {
                "start": f.start,
                "end": f.end,
                "description": f.description,
                "thumbnail_path": str(f.thumbnail_path) if f.thumbnail_path else None,
            }
            for f in c.fragments
        ],
    }


@mcp.tool()
def analyze_video(path: str, scene_threshold: float = 0.3) -> dict:
    """任意の映像ファイル(WSL絶対パス)に段階1(境界検出)を実行し、fragment候補を返す。

    media_catalogに登録済みのCH1〜4以外の映像に対しても、その場でfragment化
    できるようにする(semantic-tagging-experiment.md 8節)。段階2(言語化)・
    段階3(統合)はこのツールの範囲外で、返されたframe_pathの画像をClaudeが
    見て行う想定(video_fragmentation.pyの設計方針をそのまま踏襲)。
    """
    video_path = Path(path)
    if not video_path.exists():
        raise ValueError(f"ファイルが見つかりません: {path}")

    digest = hashlib.sha1(str(video_path.resolve()).encode()).hexdigest()[:10]
    out_dir = Path(tempfile.gettempdir()) / "ai-nas-manager-analysis" / f"{video_path.stem}_{digest}"

    fragments = video_fragmentation.fragment_video(video_path, out_dir, scene_threshold=scene_threshold)
    return {
        "path": str(video_path),
        "duration": video_fragmentation.get_duration(video_path),
        "fragments": [
            {"index": f.index, "start": f.start, "end": f.end, "frame_path": str(f.frame_path)}
            for f in fragments
        ],
    }


@mcp.tool()
async def discover_tuners(timeout_sec: float = 3.0) -> list[dict]:
    """mDNSでネットワーク上のTuner(仮想Tunerを含む)を発見する。"""
    tuners = await anyio.to_thread.run_sync(_discover_tuners, timeout_sec)
    return [{"name": t.name, "host": t.host, "port": t.port} for t in tuners]


async def _resolve_tuner(
    tuner_name: str | None, host: str | None, port: int | None
) -> tuple[str, int]:
    """tuner_name(mDNS名前解決)またはhost+port直接指定から接続先を決定する。"""
    if host is not None and port is not None:
        return host, port

    if not tuner_name:
        raise ValueError("tuner_name か host+port のどちらかを指定してください")

    tuners: list[TunerInfo] = await anyio.to_thread.run_sync(_discover_tuners, 3.0)
    match = next((t for t in tuners if t.name == tuner_name), None)
    if match is None:
        raise ValueError(f"Tuner '{tuner_name}' が見つかりませんでした")
    return match.host, match.port


@mcp.tool()
async def get_tuner_status(
    tuner_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> dict:
    """指定したTunerの状態を取得する。tuner_name指定時はmDNSで名前解決する(host+port直接指定も可)。"""
    resolved_host, resolved_port = await _resolve_tuner(tuner_name, host, port)
    return await _tuner_client.get_status(resolved_host, resolved_port)


@mcp.tool()
async def get_tuner_channels(
    tuner_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> list[str]:
    """指定したTunerのチャンネル一覧を取得する。tuner_name指定時はmDNSで名前解決する(host+port直接指定も可)。"""
    resolved_host, resolved_port = await _resolve_tuner(tuner_name, host, port)
    return await _tuner_client.list_channels(resolved_host, resolved_port)


@mcp.tool()
async def get_tuner_program_guide(
    tuner_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    channel: str | None = None,
    genre: str | None = None,
    keyword: str | None = None,
    at: str | None = None,
) -> list[dict]:
    """指定したTunerの番組表を取得する。channel/genre/keyword/at(ISO8601時刻)で絞り込み可能。

    tuner_name指定時はmDNSで名前解決する(host+port直接指定も可)。
    """
    resolved_host, resolved_port = await _resolve_tuner(tuner_name, host, port)
    return await _tuner_client.get_program_guide(
        resolved_host, resolved_port, channel=channel, genre=genre, keyword=keyword, at=at
    )


if __name__ == "__main__":
    mcp.run()
