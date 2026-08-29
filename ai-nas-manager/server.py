"""AI NAS manager MCPサーバーの骨組み。

将来的にRockchip NAS上で動かし、メディア管理(整理・分類など)を
自然言語で操作できるようにするための土台。
v-01ではTuner(仮想Tuner)発見・状態取得・番組表取得のMCP連携パターンを追加した。
"""

import hashlib
import tempfile
from pathlib import Path

import anyio

from mcp.server.mcpserver import MCPServer

import media_catalog
import tuner_client as _tuner_client
import video_fragmentation
from discovery import TunerInfo, discover_tuners as _discover_tuners

mcp = MCPServer("ai-nas-manager")


@mcp.tool()
def ping() -> str:
    """疎通確認用のダミーツール。"""
    return "pong"


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
    """タイトル・tag・fragmentの説明文にkeywordを含むチャンネルを検索する。

    ai-nas-manager/docs/semantic-tagging-experiment.md 9節のNASインデックス構想の
    最小実装。将来的には大規模なライブラリに対する高速検索(SQLite等)に
    置き換わる想定だが、呼び出し側から見たインターフェースはそのまま使える
    よう設計している。
    """
    return [
        {"channel": c.channel, "title": c.title, "tag": c.tag}
        for c in media_catalog.search_channels(keyword)
    ]


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
            {"start": f.start, "end": f.end, "description": f.description}
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
