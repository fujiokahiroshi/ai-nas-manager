"""Admission policy separating transient Fragment candidates from permanent records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    state: str
    score: float
    reasons: tuple[str, ...]

    @property
    def confirmed(self) -> bool:
        return self.state == "confirmed"


def evaluate_admission(item: dict[str, object]) -> AdmissionDecision:
    """Classify one Gemma result without treating model confidence as truth."""

    if bool(item.get("manual_marker", False)):
        return AdmissionDecision("confirmed", 1.0, ("manual_marker",))

    confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
    observation = str(item.get("observation_ja", item.get("observation", ""))).strip()
    action = str(item.get("action_ja", item.get("action", ""))).strip()
    change = str(item.get("change_from_previous_ja", item.get("change_text", ""))).strip()
    raw_objects = item.get("objects", [])
    objects = [str(value).strip() for value in raw_objects if str(value).strip()] if isinstance(raw_objects, list) else []
    trigger_reason = str(item.get("trigger_reason", ""))
    trusted_trigger = trigger_reason in {"fused_change", "impact_sound", "subtitle"}

    reasons: list[str] = []
    score = confidence * 0.70
    if len(observation) >= 4:
        score += 0.12
        reasons.append("observation")
    if action or change or objects:
        score += 0.08
        reasons.append("structured_detail")
    if trusted_trigger:
        score += 0.10
        reasons.append("trusted_trigger")
    score = min(1.0, score)

    has_semantic_evidence = len(observation) >= 4 and bool(action or change or objects)
    if score >= 0.76 and has_semantic_evidence:
        state = "confirmed"
    elif score >= 0.50 and observation:
        state = "review"
    else:
        state = "candidate"
    return AdmissionDecision(state, round(score, 4), tuple(reasons))


def confirmed_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [record for record in records if record.get("admission_state") == "confirmed"]
