from __future__ import annotations

import json

from scene_semantics import (
    LMStudioSceneSummaryClient,
    SceneTextEvidence,
    build_scene_evidence,
    summarize_payload,
)


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


def test_lm_studio_scene_summary_retries_incomplete_json(monkeypatch) -> None:
    contents = iter([
        '{"summary_ja":"incomplete"',
        '{"summary_ja":"complete","activities":[],"objects":[],"important_change_ja":"","confidence":0.8}',
    ])
    output_token_limits = []

    class FakeResponse:
        def __init__(self, content: str) -> None:
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps({
                "output": [{"type": "message", "content": self.content}],
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        del timeout
        payload = json.loads(request.data.decode("utf-8"))
        output_token_limits.append(payload["max_output_tokens"])
        return FakeResponse(next(contents))

    monkeypatch.setattr("scene_semantics.urllib.request.urlopen", fake_urlopen)
    evidence = SceneTextEvidence("scene-1", 0, 1_000, ({
        "timestamp_ms": 0,
        "observation": "sample",
    },))
    result = LMStudioSceneSummaryClient(max_output_tokens=320).summarize(evidence)

    assert result["summary_ja"] == "complete"
    assert output_token_limits == [320, 640]
