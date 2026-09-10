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
    quiet_ms: int = 2_000
    min_update_ms: int = 1_500
    min_fragment_ms: int = 1_000
    max_fragment_ms: int = 15_000
    pixel_stride: int = 4
    histogram_bins: int = 16
    ewma_alpha: float = 0.08
    representative_limit: int = 3
    open_on_start: bool = True

    def __post_init__(self) -> None:
        probabilities = (
            self.open_threshold,
            self.update_threshold,
            self.quiet_threshold,
            self.hard_cut_threshold,
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
        self.last_signals = SignalVector()

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
        active = composite >= self.config.quiet_threshold
        trigger = (
            (first_frame and self.config.open_on_start)
            or hard_cut
            or composite >= self.config.open_threshold
            or subtitle_changed
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
                reason = (
                    "stream_start" if first_frame
                    else "hard_cut" if hard_cut
                    else "subtitle" if subtitle_changed
                    else "adaptive_change"
                )
                events.append(self._event(EventKind.OPEN, frame, signals, reason, subtitle))
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
        if update_due and (composite >= self.config.update_threshold or subtitle_changed):
            self._open.revision += 1
            self._open.last_emit_ms = frame.timestamp_ms
            reason = "subtitle" if subtitle_changed else "continued_change"
            events.append(self._event(EventKind.UPDATE, frame, signals, reason, subtitle))
        return events

    def finish(self) -> list[FragmentEvent]:
        if self._open is None or self._previous is None:
            return []
        zero = SignalVector()
        self._open.revision += 1
        event = self._event(EventKind.CLOSE, self._previous, zero, "end_of_stream", self._last_subtitle)
        self._open = None
        return [event]
