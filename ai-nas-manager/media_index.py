"""ライブラリ全体の映像を、動的に登録・検索できる永続インデックス。

media_catalog.py(CH1〜4、決め打ちのチューナー風チャンネル)とは別に、
Claudeが自律的に発見・解析した任意の映像を登録していく場所。
semantic-tagging-experiment.md 9節のNASインデックス構想の実装。
今はJSONファイルに保存する最小実装(将来SQLite等に置き換える場合も、
このモジュールの関数シグネチャは変えずに済むよう設計している)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parent / "media_index.json"
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


@dataclass(frozen=True)
class FragmentEntry:
    start: float
    end: float
    description: str
    thumbnail_path: str | None = None


@dataclass(frozen=True)
class MediaEntry:
    path: str
    title: str
    tag: str
    fragments: list[FragmentEntry] = field(default_factory=list)


def _load_raw() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _save_raw(entries: list[dict]) -> None:
    INDEX_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def list_registered() -> list[MediaEntry]:
    """登録済みの全エントリを返す。"""
    return [
        MediaEntry(
            path=e["path"],
            title=e["title"],
            tag=e["tag"],
            fragments=[FragmentEntry(**f) for f in e.get("fragments", [])],
        )
        for e in _load_raw()
    ]


def register(path: str, title: str, tag: str, fragments: list[dict]) -> MediaEntry:
    """映像をインデックスに登録する(同じpathが既にあれば上書き)。"""
    entries = [e for e in _load_raw() if e["path"] != path]
    entries.append({"path": path, "title": title, "tag": tag, "fragments": fragments})
    _save_raw(entries)
    return MediaEntry(
        path=path,
        title=title,
        tag=tag,
        fragments=[FragmentEntry(**f) for f in fragments],
    )


def list_pending(scan_dir: str) -> list[str]:
    """scan_dir直下の映像ファイルのうち、まだ登録されていないものを返す。"""
    directory = Path(scan_dir)
    if not directory.is_dir():
        raise ValueError(f"ディレクトリが見つかりません: {scan_dir}")

    registered_paths = {e.path for e in list_registered()}
    candidates = sorted(
        str(p)
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    return [p for p in candidates if p not in registered_paths]


def search(keyword: str) -> list[MediaEntry]:
    """title/tag/fragment説明文にkeywordを含む登録済みエントリを検索する(大小文字無視)。"""
    needle = keyword.lower()
    matched: list[MediaEntry] = []
    for entry in list_registered():
        haystack = entry.title + entry.tag + "".join(f.description for f in entry.fragments)
        if needle in haystack.lower():
            matched.append(entry)
    return matched
