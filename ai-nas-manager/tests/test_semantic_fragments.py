from semantic_fragments import build_digest, build_semantic_fragment


def test_build_semantic_fragment_extracts_meaning_from_scene() -> None:
    fragment = build_semantic_fragment("ここに3人の子供が野球をしている。")

    assert fragment["subject"] == "子供"
    assert "野球" in fragment["tags"]
    assert "遊び" in fragment["tags"]
    assert "笑顔" in fragment["tags"] or "明るい" in fragment["tags"]
    assert fragment["narrative_role"] in {"opening", "activity"}


def test_build_digest_summarizes_multiple_scene_fragments() -> None:
    scenes = [
        "ここに3人の子供が野球をしている。",
        "ボールが飛んで、笑顔が広がる。",
        "大人が見守る中、楽しさが増していく。",
    ]

    digest = build_digest(scenes)

    assert "野球" in digest["theme_tags"]
    assert "子供" in digest["theme_tags"]
    assert "遊び" in digest["theme_tags"]
    assert "楽しい" in digest["summary"] or "楽しさ" in digest["summary"]
