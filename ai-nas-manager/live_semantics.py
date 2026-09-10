"""Latest-revision queue and LM Studio vision adapter for live fragments."""

from __future__ import annotations

import base64
import json
import threading
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FragmentEvidence:
    fragment_id: str
    revision: int
    source_timestamp_ms: int
    jpeg: bytes
    metadata: dict[str, object]


class LatestEvidenceQueue:
    """Thread-safe bounded queue retaining only the newest pending revision."""

    def __init__(self, max_fragments: int = 4) -> None:
        if max_fragments <= 0:
            raise ValueError("max_fragments must be positive")
        self.max_fragments = max_fragments
        self._items: OrderedDict[str, FragmentEvidence] = OrderedDict()
        self._condition = threading.Condition()
        self._closed = False
        self.replaced = 0
        self.dropped = 0

    def put(self, evidence: FragmentEvidence) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("queue is closed")
            if evidence.fragment_id in self._items:
                del self._items[evidence.fragment_id]
                self.replaced += 1
            elif len(self._items) >= self.max_fragments:
                self._items.popitem(last=False)
                self.dropped += 1
            self._items[evidence.fragment_id] = evidence
            self._condition.notify()

    def get(self) -> FragmentEvidence | None:
        with self._condition:
            self._condition.wait_for(lambda: bool(self._items) or self._closed)
            if self._items:
                _, evidence = self._items.popitem(last=False)
                return evidence
            return None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def parse_json_message(content: str) -> dict[str, object]:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3:
            content = "\n".join(lines[1:-1]).strip()
            if content.lower().startswith("json"):
                content = content[4:].lstrip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response did not contain a complete JSON object")
    value = json.loads(content[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("response JSON must be an object")
    return value


@dataclass(slots=True)
class LMStudioVisionClient:
    base_url: str = "http://127.0.0.1:1234"
    model: str = "gemma4-12b-qat"
    timeout_seconds: float = 45.0
    max_output_tokens: int = 140

    def analyze(self, evidence: FragmentEvidence, previous_scene: str = "") -> dict[str, object]:
        encoded = base64.b64encode(evidence.jpeg).decode("ascii")
        prompt = (
            "This is one time-stamped evidence image from a video fragment. "
            "Describe only facts visible in the image. Detector labels are hints and may be wrong. "
            "Respond with one compact JSON object, without markdown, using keys: "
            "observation_ja, objects, action_ja, change_from_previous_ja, confidence. "
            "Write Japanese strings. observation_ja is at most 60 Japanese characters; "
            "action_ja and change_from_previous_ja are each at most 30 characters; "
            "objects has at most 5 short nouns; confidence is 0..1. "
            "Treat the detector group two_wheeler as bicycle-or-motorcycle and do not choose "
            "between them unless the image is clear.\n"
            f"Fragment evidence: {json.dumps(evidence.metadata, ensure_ascii=False)}\n"
            f"Previous scene text: {previous_scene[-1000:] if previous_scene else 'none'}"
        )
        payload = {
            "model": self.model,
            "system_prompt": (
                "あなたはAI NASの映像観察バックエンドです。画像に見える事実と推測を分離し、"
                "人数など不確実な値は断定しすぎないでください。"
            ),
            "input": [
                {"type": "text", "content": prompt},
                {"type": "image", "data_url": f"data:image/jpeg;base64,{encoded}"},
            ],
            "temperature": 0,
            "max_output_tokens": self.max_output_tokens,
            "reasoning": "off",
            "store": False,
        }
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/v1/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        messages = [item.get("content", "") for item in body.get("output", []) if item.get("type") == "message"]
        if not messages:
            raise ValueError("LM Studio response did not contain a message")
        result = parse_json_message(messages[-1])
        result.update({
            "fragment_id": evidence.fragment_id,
            "revision": evidence.revision,
            "source_timestamp_ms": evidence.source_timestamp_ms,
            "lm_stats": body.get("stats", {}),
        })
        return result
