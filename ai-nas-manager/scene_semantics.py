"""Build one grounded Gemma summary from multiple Fragment descriptions."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from live_semantics import parse_json_message
from pc_app import fragment_records, scene_boundaries, scene_records


@dataclass(frozen=True, slots=True)
class SceneTextEvidence:
    scene_id: str
    start_ms: int
    end_ms: int
    fragments: tuple[dict[str, object], ...]


class SceneSummarizer(Protocol):
    def summarize(self, evidence: SceneTextEvidence) -> dict[str, object]: ...


def _description(item: dict[str, object]) -> str:
    return str(item.get("user_text") or item.get("observation") or "").strip()


def _is_redundant_revision(previous: dict[str, object], current: dict[str, object]) -> bool:
    """Return true only when a revision repeats the same temporal evidence."""

    if str(previous["fragment_id"]) != str(current["fragment_id"]):
        return False
    before = "".join(_description(previous).split())
    after = "".join(_description(current).split())
    if not before or not after:
        return before == after
    change = str(current.get("change_text", "")).strip().casefold()
    no_change = change in {"", "変化なし", "変更なし", "なし", "no change", "unchanged"}
    return no_change and SequenceMatcher(None, before, after).ratio() >= 0.72


def build_scene_evidence(payload: dict[str, object]) -> list[SceneTextEvidence]:
    """Keep temporal changes while compacting redundant Fragment revisions."""

    records = fragment_records(payload)
    boundaries, method = scene_boundaries(payload)
    scenes = scene_records(records, boundaries, method)
    result = []
    for scene in scenes:
        fragments_list: list[dict[str, object]] = []
        ordered = sorted(
            scene["fragments"],
            key=lambda item: (int(item["timestamp_ms"]), int(item["revision"])),
        )
        for item in ordered:
            if fragments_list and _is_redundant_revision(fragments_list[-1], item):
                fragments_list[-1] = item
            else:
                fragments_list.append(item)
        fragments = tuple(fragments_list)
        result.append(SceneTextEvidence(
            str(scene["id"]),
            int(scene["start_ms"]),
            int(scene["end_ms"]),
            fragments,
        ))
    return result


@dataclass(slots=True)
class LMStudioSceneSummaryClient:
    base_url: str = "http://127.0.0.1:1234"
    model: str = "gemma4-12b-qat"
    timeout_seconds: float = 45.0
    max_output_tokens: int = 320
    max_attempts: int = 2

    def summarize(self, evidence: SceneTextEvidence) -> dict[str, object]:
        observations = [{
            "sequence": index + 1,
            "time_seconds": round(int(item["timestamp_ms"]) / 1000, 2),
            "observation": item.get("user_text") or item.get("observation") or "",
            "action": item.get("action", ""),
            "change": item.get("change_text", ""),
            "objects": item.get("objects", []),
            "confidence": item.get("confidence", 0.0),
        } for index, item in enumerate(evidence.fragments)]
        prompt = (
            "次の時系列Fragmentは同じ映像Sceneに属します。重複する説明を統合し、"
            "Sceneの前半・中盤・後半を時間順に確認して、Scene全体で継続している内容と重要な変化を要約してください。"
            "前半と後半で状況が異なる場合は『前半は…、その後…』の形で両方を必ず含めてください。"
            "入力にない人物・物体・因果関係を推測しないでください。矛盾する説明は、"
            "確信度の高い記述を優先し、断定できない内容を除外してください。"
            "JSONオブジェクトだけを返してください。キーは summary_ja、activities、objects、"
            "important_change_ja、confidence。summary_jaは120文字以内、important_change_jaは60文字以内、"
            "activitiesとobjectsは最大5個の短い日本語文字列、confidenceは0から1です。\n"
            f"Scene: {evidence.start_ms / 1000:.2f}–{evidence.end_ms / 1000:.2f}秒\n"
            f"Fragments: {json.dumps(observations, ensure_ascii=False)}"
        )
        payload = {
            "model": self.model,
            "system_prompt": (
                "あなたはAI NASの映像Scene要約バックエンドです。与えられた時系列観察だけを統合し、"
                "見えていない事実を追加しません。"
            ),
            "input": prompt,
            "temperature": 0,
            "max_output_tokens": self.max_output_tokens,
            "reasoning": "off",
            "store": False,
        }
        last_error: ValueError | None = None
        for attempt in range(max(1, self.max_attempts)):
            payload["max_output_tokens"] = self.max_output_tokens * (attempt + 1)
            request = urllib.request.Request(
                f"{self.base_url.rstrip('/')}/api/v1/chat",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            messages = [
                item.get("content", "")
                for item in body.get("output", [])
                if item.get("type") == "message"
            ]
            try:
                if not messages:
                    raise ValueError("LM Studio response did not contain a Scene summary")
                return parse_json_message(messages[-1])
            except ValueError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


def summarize_payload(payload: dict[str, object], summarizer: SceneSummarizer) -> list[dict[str, object]]:
    summaries = []
    for scene in build_scene_evidence(payload):
        if not scene.fragments:
            continue
        if len(scene.fragments) == 1:
            item = scene.fragments[0]
            result: dict[str, object] = {
                "summary_ja": item.get("user_text") or item.get("observation") or "",
                "activities": [item["action"]] if item.get("action") else [],
                "objects": item.get("objects", []),
                "important_change_ja": item.get("change_text", ""),
                "confidence": item.get("confidence", 0.0),
                "method": "single_fragment_passthrough",
            }
        else:
            result = dict(summarizer.summarize(scene))
            result["method"] = "gemma_multi_fragment"
        result.update({
            "scene_id": scene.scene_id,
            "start_ms": scene.start_ms,
            "end_ms": scene.end_ms,
            "evidence_count": len(scene.fragments),
        })
        summaries.append(result)
    return summaries
