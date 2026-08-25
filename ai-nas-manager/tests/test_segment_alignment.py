from segment_alignment import build_demo_alignment


def test_demo_alignment_connects_video_audio_and_semantic_fragment() -> None:
    alignments = build_demo_alignment()

    assert len(alignments) >= 2
    assert all(item.confidence > 0 for item in alignments)
    assert any(item.video.scene_summary.startswith("3人の子供") for item in alignments)
    assert any("野球" in item.semantic.tags for item in alignments)
    assert any("笑顔" in item.semantic.tags for item in alignments)
