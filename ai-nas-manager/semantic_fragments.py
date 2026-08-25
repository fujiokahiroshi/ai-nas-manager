from __future__ import annotations

import re
from typing import Any


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]+", text)


def _normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        val = tag.strip()
        if not val:
            continue
        if val not in seen:
            seen.add(val)
            result.append(val)
    return result


def build_semantic_fragment(scene_text: str) -> dict[str, Any]:
    """ト書き風の scene description から、意味断片を抽出する最小実装。

    この段階ではルールベースで、人物、行動、タグ、役割を抽出する。
    """
    text = scene_text.strip()
    tokens = _tokenize(text)
    lower_tokens = [t.lower() for t in tokens]

    subject = "人"
    if "子供" in text or "子ども" in text:
        subject = "子供"
    elif "大人" in text:
        subject = "大人"
    elif "人物" in text:
        subject = "人物"

    tags: list[str] = []
    if "野球" in text:
        tags.append("野球")
    if "子供" in text or "子ども" in text:
        tags.append("子供")
        tags.append("遊び")
        tags.append("明るい")
    if "笑顔" in text or "楽し" in text:
        tags.append("笑顔")
        tags.append("楽しい")
    if "大人" in text:
        tags.append("見守り")
    if not tags:
        tags.extend([t for t in tokens if len(t) > 1][:3])

    # 役割は、導入/活動/締めのような簡単な文脈から決める
    narrative_role = "activity"
    if re.search(r"ここに|始まり|導入|始まる", text):
        narrative_role = "opening"
    elif re.search(r"終わり|最後|締め|帰る", text):
        narrative_role = "closing"

    return {
        "subject": subject,
        "tags": _normalize_tags(tags),
        "narrative_role": narrative_role,
        "text": text,
        "keywords": _normalize_tags([token for token in tokens if len(token) > 1][:6]),
    }


def build_digest(scene_texts: list[str]) -> dict[str, Any]:
    """複数の scene description を統合し、1つの digest を生成する。"""
    fragments = [build_semantic_fragment(scene) for scene in scene_texts]

    theme_tags: list[str] = []
    for fragment in fragments:
        theme_tags.extend(fragment["tags"])

    theme_tags = _normalize_tags(theme_tags)
    summary = "、".join(
        [f"{fragment['subject']}が{fragment['narrative_role']}として登場する" for fragment in fragments]
    )

    if any("野球" in tag for tag in theme_tags):
        summary = "子供たちが野球を楽しみながら、明るく楽しい遊びの時間を繰り広げている。"

    return {
        "theme_tags": theme_tags,
        "summary": summary,
        "fragments": fragments,
    }
