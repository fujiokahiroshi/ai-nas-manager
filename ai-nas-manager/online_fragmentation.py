"""Causal, multi-signal video fragment generation.

This module deliberately has no ML or imaging dependency.  The product adapter
can feed 320x180 luma frames from Rockchip MPP/RGA and optional object, audio,
and subtitle change scores.  Decisions are emitted while the stream is alive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import sqrt


@dataclass(frozen=True, slots=True)
class GrayFrame:
    timestamp_ms: int
    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("invalid frame metadata")
        if len(self.pixels) != self.width * self.height:
            raise ValueError("pixels must contain width * height luma bytes")


@dataclass(frozen=True, slots=True)
class SignalVector:
    luma_difference: float = 0.0
    histogram_difference: float = 0.0
    edge_difference: float = 0.0
    changed_pixel_ratio: float = 0.0
    adaptive_novelty: float = 0.0
    object_change: float = 0.0
    audio_change: float = 0.0
    subtitle_change: float = 0.0
    composite: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            name: round(float(getattr(self, name)), 6)
            for name in self.__dataclass_fields__
        }


class EventKind(str, Enum):
    OPEN = "open"
    UPDATE = "update"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class FragmentEvent:
    kind: EventKind
    fragment_id: str
    revision: int
    start_ms: int
    observed_ms: int
    reason: str
    signals: SignalVector
    subtitle: str = ""
    representative_times_ms: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "fragment_id": self.fragment_id,
            "revision": self.revision,
            "start_ms": self.start_ms,
            "observed_ms": self.observed_ms,
            "reason": self.reason,
            "subtitle": self.subtitle,
            "representative_times_ms": list(self.representative_times_ms),
            "signals": self.signals.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class OnlineFragmentConfig:
    open_threshold: float = 0.18
    update_threshold: float = 0.15
    quiet_threshold: float = 0.05
    hard_cut_threshold: float = 0.42
    audio_trigger_threshold: float = 0.72
    quiet_ms: int = 2_000
    min_update_ms: int = 1_500
    min_fragment_ms: int = 1_000
    max_fragment_ms: int = 15_000
    pixel_stride: int = 4
    histogram_bins: int = 16
    ewma_alpha: float = 0.08
    representative_limit: int = 3
    open_on_start: bool = True
    fusion_enabled: bool = False
    visual_trigger_threshold: float = 0.24
    visual_strong_threshold: float = 0.38
    visual_release_threshold: float = 0.10
    object_trigger_threshold: float = 0.55
    object_release_threshold: float = 0.20
    audio_release_threshold: float = 0.30
    confirming_signals: int = 2
    visual_cooldown_ms: int = 3_000
    object_cooldown_ms: int = 2_500
    audio_cooldown_ms: int = 4_000
    subtitle_cooldown_ms: int = 1_500
    fusion_cooldown_ms: int = 2_500

    def __post_init__(self) -> None:
        probabilities = (
            self.open_threshold,
            self.update_threshold,
            self.quiet_threshold,
            self.hard_cut_threshold,
            self.audio_trigger_threshold,
            self.visual_trigger_threshold,
            self.visual_strong_threshold,
            self.visual_release_threshold,
            self.object_trigger_threshold,
            self.object_release_threshold,
            self.audio_release_threshold,
            self.ewma_alpha,
        )
        if any(value < 0.0 or value > 1.0 for value in probabilities):
            raise ValueError("thresholds and ewma_alpha must be in 0..1")
        if self.histogram_bins <= 1 or 256 % self.histogram_bins:
            raise ValueError("histogram_bins must divide 256")
        if self.pixel_stride <= 0 or self.representative_limit <= 0:
            raise ValueError("sampling values must be positive")
        if min(self.quiet_ms, self.min_update_ms, self.min_fragment_ms, self.max_fragment_ms) <= 0:
            raise ValueError("timing values must be positive")
        cooldowns = (
            self.visual_cooldown_ms,
            self.object_cooldown_ms,
            self.audio_cooldown_ms,
            self.subtitle_cooldown_ms,
            self.fusion_cooldown_ms,
        )
        if any(value < 0 for value in cooldowns) or self.confirming_signals < 2:
            raise ValueError("cooldowns and confirming_signals are invalid")


def fragment_config(profile: str = "fused-v2-balanced") -> OnlineFragmentConfig:
    """Return a named, reproducible detector profile."""

    if profile in {"fused-v2", "fused-v2-conservative"}:
        return OnlineFragmentConfig(fusion_enabled=True)
    if profile == "fused-v2-balanced":
        return OnlineFragmentConfig(
            fusion_enabled=True,
            visual_trigger_threshold=0.18,
            visual_strong_threshold=0.28,
            visual_release_threshold=0.14,
            audio_trigger_threshold=0.78,
            visual_cooldown_ms=2_000,
            object_cooldown_ms=2_000,
            audio_cooldown_ms=3_000,
            fusion_cooldown_ms=2_000,
        )
    if profile == "legacy-v1":
        return OnlineFragmentConfig()
    raise ValueError(f"unknown fragment profile: {profile}")


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def visual_signals(
    previous: GrayFrame,
    current: GrayFrame,
    *,
    stride: int = 4,
    histogram_bins: int = 16,
) -> tuple[float, float, float, float]:
    """Return luma, histogram, edge and changed-pixel distances in 0..1."""

    if (previous.width, previous.height) != (current.width, current.height):
        return 1.0, 1.0, 1.0, 1.0
    old, new = previous.pixels, current.pixels
    indexes = range(0, len(new), stride)
    count = (len(new) + stride - 1) // stride
    absolute = 0
    changed = 0
    old_hist = [0] * histogram_bins
    new_hist = [0] * histogram_bins
    bin_width = 256 // histogram_bins
    for index in indexes:
        delta = abs(new[index] - old[index])
        absolute += delta
        changed += delta >= 20
        old_hist[min(old[index] // bin_width, histogram_bins - 1)] += 1
        new_hist[min(new[index] // bin_width, histogram_bins - 1)] += 1
    luma = absolute / (count * 255.0)
    histogram = sum(abs(a - b) for a, b in zip(old_hist, new_hist)) / (2.0 * count)

    # Compare gradient magnitudes. Sampling every ``stride`` rows and columns
    # keeps this inexpensive enough for small ARM cores.
    width, height = current.width, current.height
    edge_total = 0
    edge_count = 0
    step = max(1, stride)
    for y in range(step, height, step):
        row = y * width
        prior_row = (y - step) * width
        for x in range(step, width, step):
            i = row + x
            old_gradient = abs(old[i] - old[i - step]) + abs(old[i] - old[prior_row + x])
            new_gradient = abs(new[i] - new[i - step]) + abs(new[i] - new[prior_row + x])
            edge_total += abs(new_gradient - old_gradient)
            edge_count += 1
    edge = edge_total / (edge_count * 510.0) if edge_count else 0.0
    return _clip(luma), _clip(histogram), _clip(edge), changed / count


@dataclass(slots=True)
class _AdaptiveBaseline:
    alpha: float
    mean: float = 0.0
    variance: float = 0.0
    samples: int = 0

    def novelty(self, value: float) -> float:
        if self.samples < 3:
            result = value
        else:
            deviation = sqrt(max(self.variance, 0.0))
            result = _clip((value - self.mean) / max(0.025, 3.0 * deviation))
        previous_mean = self.mean
        if self.samples == 0:
            self.mean = value
        else:
            self.mean = (1.0 - self.alpha) * self.mean + self.alpha * value
        self.variance = (1.0 - self.alpha) * self.variance + self.alpha * (value - previous_mean) ** 2
        self.samples += 1
        return result


@dataclass(slots=True)
class _OpenFragment:
    fragment_id: str
    start_ms: int
    revision: int
    last_activity_ms: int
    last_emit_ms: int
    representatives: list[tuple[float, int]] = field(default_factory=list)


class OnlineMultiSignalFragmenter:
    """Streaming fragmenter with adaptive thresholds and hysteresis."""

    def __init__(self, source_id: str, config: OnlineFragmentConfig | None = None) -> None:
        self.source_id = source_id
        self.config = config or OnlineFragmentConfig()
        self._previous: GrayFrame | None = None
        self._last_subtitle = ""
        self._baseline = _AdaptiveBaseline(self.config.ewma_alpha)
        self._open: _OpenFragment | None = None
        self._sequence = 0
        self._signal_armed = {"visual": True, "object": True, "audio": True}
        self._last_signal_emit_ms: dict[str, int] = {}
        self.last_signals = SignalVector()

    def _fused_reason(
        self,
        timestamp_ms: int,
        *,
        visual_score: float,
        object_change: float,
        audio_change: float,
        audio_label: str,
        subtitle_changed: bool,
    ) -> tuple[str | None, tuple[str, ...]]:
        """Return a debounced semantic reason and contributing signals."""

        config = self.config
        releases = {
            "visual": visual_score < config.visual_release_threshold,
            "object": object_change < config.object_release_threshold,
            "audio": audio_change < config.audio_release_threshold,
        }
        for name, released in releases.items():
            if released:
                self._signal_armed[name] = True

        audio_is_event = audio_label in {"impact_candidate", "transient_candidate"} or not audio_label
        active = {
            "visual": visual_score >= config.visual_trigger_threshold,
            "object": object_change >= config.object_trigger_threshold,
            "audio": audio_change >= config.audio_trigger_threshold and audio_is_event,
            "subtitle": subtitle_changed,
        }
        cooldowns = {
            "visual": config.visual_cooldown_ms,
            "object": config.object_cooldown_ms,
            "audio": config.audio_cooldown_ms,
            "subtitle": config.subtitle_cooldown_ms,
        }

        def eligible(name: str) -> bool:
            armed = True if name == "subtitle" else self._signal_armed[name]
            elapsed = timestamp_ms - self._last_signal_emit_ms.get(name, -10**12)
            return active[name] and armed and elapsed >= cooldowns[name]

        contributors = tuple(name for name, present in active.items() if present)
        fusion_elapsed = timestamp_ms - self._last_signal_emit_ms.get("fusion", -10**12)
        if (
            len(contributors) >= config.confirming_signals
            and any(eligible(name) for name in contributors)
            and fusion_elapsed >= config.fusion_cooldown_ms
        ):
            return "fused_change", contributors
        if active["audio"] and eligible("audio"):
            return "impact_sound", ("audio",)
        if active["subtitle"] and eligible("subtitle"):
            return "subtitle", ("subtitle",)
        if visual_score >= config.visual_strong_threshold and eligible("visual"):
            return "strong_visual_change", ("visual",)
        return None, ()

    def _consume_signals(self, timestamp_ms: int, contributors: tuple[str, ...]) -> None:
        if len(contributors) >= self.config.confirming_signals:
            self._last_signal_emit_ms["fusion"] = timestamp_ms
        for name in contributors:
            self._last_signal_emit_ms[name] = timestamp_ms
            if name in self._signal_armed:
                self._signal_armed[name] = False

    @property
    def is_open(self) -> bool:
        return self._open is not None

    def _remember_representative(self, timestamp_ms: int, score: float) -> None:
        assert self._open is not None
        candidates = self._open.representatives
        candidates.append((score, timestamp_ms))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        del candidates[self.config.representative_limit :]

    def _representative_times(self) -> tuple[int, ...]:
        assert self._open is not None
        return tuple(sorted(timestamp for _, timestamp in self._open.representatives))

    def _event(
        self,
        kind: EventKind,
        frame: GrayFrame,
        signals: SignalVector,
        reason: str,
        subtitle: str,
    ) -> FragmentEvent:
        assert self._open is not None
        return FragmentEvent(
            kind=kind,
            fragment_id=self._open.fragment_id,
            revision=self._open.revision,
            start_ms=self._open.start_ms,
            observed_ms=frame.timestamp_ms,
            reason=reason,
            signals=signals,
            subtitle=subtitle,
            representative_times_ms=self._representative_times(),
        )

    def process(
        self,
        frame: GrayFrame,
        *,
        object_change: float = 0.0,
        audio_change: float = 0.0,
        audio_label: str = "",
        subtitle: str = "",
    ) -> list[FragmentEvent]:
        if self._previous and frame.timestamp_ms < self._previous.timestamp_ms:
            raise ValueError("frames must be timestamp ordered")
        if any(value < 0.0 or value > 1.0 for value in (object_change, audio_change)):
            raise ValueError("external change scores must be in 0..1")

        subtitle_changed = bool(subtitle and subtitle != self._last_subtitle)
        subtitle_score = 1.0 if subtitle_changed else 0.0
        first_frame = self._previous is None
        if first_frame:
            luma = histogram = edge = changed_ratio = visual = novelty = 0.0
        else:
            luma, histogram, edge, changed_ratio = visual_signals(
                self._previous,
                frame,
                stride=self.config.pixel_stride,
                histogram_bins=self.config.histogram_bins,
            )
            visual = 0.30 * luma + 0.30 * histogram + 0.15 * edge + 0.25 * changed_ratio
            novelty = self._baseline.novelty(visual)

        composite = _clip(
            0.50 * max(visual, novelty)
            + 0.25 * object_change
            + 0.10 * audio_change
            + 0.15 * subtitle_score
        )
        signals = SignalVector(
            luma, histogram, edge, changed_ratio, novelty,
            object_change, audio_change, subtitle_score, composite,
        )
        self.last_signals = signals
        self._previous = frame
        self._last_subtitle = subtitle
        hard_cut = histogram > self.config.hard_cut_threshold or luma > self.config.hard_cut_threshold
        audio_triggered = audio_change >= self.config.audio_trigger_threshold
        active = composite >= self.config.quiet_threshold
        fused_reason, fused_contributors = self._fused_reason(
            frame.timestamp_ms,
            visual_score=max(visual, novelty),
            object_change=object_change,
            audio_change=audio_change,
            audio_label=audio_label,
            subtitle_changed=subtitle_changed,
        ) if self.config.fusion_enabled else (None, ())
        trigger = (
            (first_frame and self.config.open_on_start)
            or hard_cut
            or (fused_reason is not None if self.config.fusion_enabled else (
                composite >= self.config.open_threshold or audio_triggered or subtitle_changed
            ))
        )
        events: list[FragmentEvent] = []

        if self._open is None:
            if trigger:
                self._sequence += 1
                self._open = _OpenFragment(
                    fragment_id=f"{self.source_id}-{frame.timestamp_ms:013d}-{self._sequence:06d}",
                    start_ms=frame.timestamp_ms,
                    revision=1,
                    last_activity_ms=frame.timestamp_ms,
                    last_emit_ms=frame.timestamp_ms,
                )
                self._remember_representative(frame.timestamp_ms, composite)
                reason = fused_reason or (
                    "stream_start" if first_frame
                    else "hard_cut" if hard_cut
                    else "audio_change" if audio_triggered
                    else "subtitle" if subtitle_changed
                    else "adaptive_change"
                )
                events.append(self._event(EventKind.OPEN, frame, signals, reason, subtitle))
                if fused_reason:
                    self._consume_signals(frame.timestamp_ms, fused_contributors)
            return events

        assert self._open is not None
        age = frame.timestamp_ms - self._open.start_ms
        if active:
            self._open.last_activity_ms = frame.timestamp_ms
            self._remember_representative(frame.timestamp_ms, composite)

        if hard_cut and age >= self.config.min_fragment_ms:
            self._open.revision += 1
            events.append(self._event(EventKind.CLOSE, frame, signals, "hard_cut", subtitle))
            self._sequence += 1
            self._open = _OpenFragment(
                fragment_id=f"{self.source_id}-{frame.timestamp_ms:013d}-{self._sequence:06d}",
                start_ms=frame.timestamp_ms,
                revision=1,
                last_activity_ms=frame.timestamp_ms,
                last_emit_ms=frame.timestamp_ms,
            )
            self._remember_representative(frame.timestamp_ms, composite)
            events.append(self._event(EventKind.OPEN, frame, signals, "hard_cut", subtitle))
            return events

        if age >= self.config.max_fragment_ms:
            self._open.revision += 1
            events.append(self._event(EventKind.CLOSE, frame, signals, "maximum_duration", subtitle))
            self._sequence += 1
            self._open = _OpenFragment(
                fragment_id=f"{self.source_id}-{frame.timestamp_ms:013d}-{self._sequence:06d}",
                start_ms=frame.timestamp_ms,
                revision=1,
                last_activity_ms=frame.timestamp_ms,
                last_emit_ms=frame.timestamp_ms,
            )
            self._remember_representative(frame.timestamp_ms, composite)
            events.append(self._event(EventKind.OPEN, frame, signals, "continued_stream", subtitle))
            return events

        quiet_for = frame.timestamp_ms - self._open.last_activity_ms
        if age >= self.config.min_fragment_ms and quiet_for >= self.config.quiet_ms:
            self._open.revision += 1
            events.append(self._event(EventKind.CLOSE, frame, signals, "quiet_timeout", subtitle))
            self._open = None
            return events

        update_due = frame.timestamp_ms - self._open.last_emit_ms >= self.config.min_update_ms
        update_triggered = (
            fused_reason is not None
            if self.config.fusion_enabled
            else composite >= self.config.update_threshold or audio_triggered or subtitle_changed
        )
        if update_due and update_triggered:
            self._open.revision += 1
            self._open.last_emit_ms = frame.timestamp_ms
            reason = fused_reason or (
                "subtitle" if subtitle_changed
                else "audio_change" if audio_triggered
                else "continued_change"
            )
            events.append(self._event(EventKind.UPDATE, frame, signals, reason, subtitle))
            if fused_reason:
                self._consume_signals(frame.timestamp_ms, fused_contributors)
        return events

    def finish(self) -> list[FragmentEvent]:
        if self._open is None or self._previous is None:
            return []
        zero = SignalVector()
        self._open.revision += 1
        event = self._event(EventKind.CLOSE, self._previous, zero, "end_of_stream", self._last_subtitle)
        self._open = None
        return [event]

    def discontinuity(self, timestamp_ms: int, reason: str = "source_disconnected") -> list[FragmentEvent]:
        """Close current state and forget frame history after an input gap."""

        events: list[FragmentEvent] = []
        if self._open is not None and self._previous is not None:
            previous = self._previous
            marker = GrayFrame(
                max(timestamp_ms, previous.timestamp_ms),
                previous.width,
                previous.height,
                previous.pixels,
            )
            self._open.revision += 1
            events.append(self._event(EventKind.CLOSE, marker, SignalVector(), reason, self._last_subtitle))
        self._open = None
        self._previous = None
        self._last_subtitle = ""
        self._baseline = _AdaptiveBaseline(self.config.ewma_alpha)
        self._signal_armed = {"visual": True, "object": True, "audio": True}
        self._last_signal_emit_ms.clear()
        return events
