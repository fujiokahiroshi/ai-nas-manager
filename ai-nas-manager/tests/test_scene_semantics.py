from __future__ import annotations

from scene_semantics import build_scene_evidence, summarize_payload


def payload() -> dict[str, object]:
    return {
        "source": "sample.mp4",
        "inferences": [
            {"fragment_id": "f1", "revision": 1, "source_timestamp_ms": 1_000, "observation_ja": "人が台所にいる", "action_ja": "調理", "objects": ["人物"], "confidence": 0.7},
            {"fragment_id": "f1", "revision": 2, "source_timestamp_ms": 2_000, "observation_ja": "人が野菜を切る", "action_ja": "調理", "objects": ["人物", "野菜"], "confidence": 0.9},
            {"fragment_id": "f2", "revision": 1, "source_timestamp_ms": 4_000, "observation_ja": "鍋に野菜を入れる", "action_ja": "調理", "objects": ["鍋", "野菜"], "confidence": 0.85},
        ],
    }


class FakeSummarizer:
    def __init__(self) -> None:
        self.evidence_count = 0

    def summarize(self, evidence):
        self.evidence_count = len(evidence.fragments)
        return {"summary_ja": "人物が野菜を切り、鍋に入れて調理している", "activities": ["調理"], "objects": ["人物", "野菜", "鍋"], "important_change_ja": "野菜を鍋に入れた", "confidence": 0.88}


def test_scene_evidence_preserves_meaningful_revision_changes() -> None:
    evidence = build_scene_evidence(payload())
    assert len(evidence) == 1
    assert [item["revision"] for item in evidence[0].fragments] == [1, 2, 1]


def test_scene_evidence_compacts_redundant_revision() -> None:
    value = payload()
    value["inferences"].insert(2, {
        "fragment_id": "f1",
        "revision": 3,
        "source_timestamp_ms": 3_000,
        "observation_ja": "人が野菜を切る",
        "action_ja": "調理",
        "change_from_previous_ja": "変化なし",
        "objects": ["人物", "野菜"],
        "confidence": 0.92,
    })
    evidence = build_scene_evidence(value)
    assert [item["revision"] for item in evidence[0].fragments] == [1, 3, 1]


def test_multiple_fragments_are_summarized_once() -> None:
    client = FakeSummarizer()
    summaries = summarize_payload(payload(), client)
    assert client.evidence_count == 3
    assert summaries[0]["method"] == "gemma_multi_fragment"
    assert summaries[0]["summary_ja"].startswith("人物が野菜を切り")
