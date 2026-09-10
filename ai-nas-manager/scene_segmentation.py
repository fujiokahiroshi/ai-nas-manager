"""Hybrid online/offline scene segmentation.

The fragment detector answers "is something worth observing happening now?".
This module answers the separate question "did the continuing semantic scene
change?".  It is dependency-free so the same state machine can run behind a
Windows decoder or a Rockchip MPP/RGA adapter.

Live decisions combine a one-sided CUSUM with a Bayesian online reset score.
They are confirmed after a small look-ahead window and can be vetoed by strong
semantic continuity.  ``pelt_boundaries`` is an offline, penalised exact change
point pass intended to refine stored boundaries after capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, pi, sqrt
from typing import Sequence


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class SceneSample:
    """One causal, time-aligned sample of change evidence."""

    timestamp_ms: int
    visual_change: float = 0.0
    object_change: float = 0.0
    audio_change: float = 0.0
    subtitle_change: float = 0.0
    semantic_change: float = 0.0
    semantic_similarity: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        values = (
            self.visual_change,
            self.object_change,
            self.audio_change,
            self.subtitle_change,
            self.semantic_change,
        )
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("scene change signals must be in 0..1")
        if self.semantic_similarity is not None and not 0.0 <= self.semantic_similarity <= 1.0:
            raise ValueError("semantic_similarity must be in 0..1")

    def fused_change(self, weights: tuple[float, float, float, float, float]) -> float:
        values = (
            self.visual_change,
            self.object_change,
            self.audio_change,
            self.subtitle_change,
            self.semantic_change,
        )
        total = sum(weights)
        if total <= 0:
            raise ValueError("scene weights must have a positive sum")
        return _clip(sum(weight * value for weight, value in zip(weights, values)) / total)


@dataclass(frozen=True, slots=True)
class SceneBoundaryEvent:
    scene_id: str
    boundary_ms: int
    emitted_ms: int
    confidence: float
    reasons: tuple[str, ...]
    fused_change: float

    def as_dict(self) -> dict[str, object]:
        return {
            "scene_id": self.scene_id,
            "boundary_ms": self.boundary_ms,
            "emitted_ms": self.emitted_ms,
            "latency_ms": self.emitted_ms - self.boundary_ms,
            "confidence": round(self.confidence, 6),
            "reasons": list(self.reasons),
            "fused_change": round(self.fused_change, 6),
        }


@dataclass(frozen=True, slots=True)
class OnlineSceneConfig:
    weights: tuple[float, float, float, float, float] = (0.38, 0.18, 0.16, 0.10, 0.18)
    cusum_drift: float = 0.055
    cusum_threshold: float = 0.34
    cusum_decay: float = 0.72
    bocpd_hazard: float = 0.035
    bocpd_threshold: float = 0.34
    bocpd_observation_variance: float = 0.012
    bocpd_prior_mean: float = 0.08
    bocpd_prior_variance: float = 0.20
    bocpd_max_run_length: int = 240
    confirmation_ms: int = 1_500
    min_scene_ms: int = 3_000
    cooldown_ms: int = 2_000
    semantic_join_threshold: float = 0.84
    hard_change_threshold: float = 0.78

    def __post_init__(self) -> None:
        probabilities = (
            *self.weights,
            self.cusum_drift,
            self.cusum_threshold,
            self.cusum_decay,
            self.bocpd_hazard,
            self.bocpd_threshold,
            self.semantic_join_threshold,
            self.hard_change_threshold,
        )
        if any(value < 0.0 or value > 1.0 for value in probabilities):
            raise ValueError("scene thresholds and weights must be in 0..1")
        if self.bocpd_observation_variance <= 0 or self.bocpd_prior_variance <= 0:
            raise ValueError("Bayesian variances must be positive")
        if self.bocpd_max_run_length <= 0:
            raise ValueError("bocpd_max_run_length must be positive")
        if min(self.confirmation_ms, self.min_scene_ms, self.cooldown_ms) < 0:
            raise ValueError("scene timing values must be non-negative")


def _normal_pdf(value: float, mean: float, variance: float) -> float:
    variance = max(variance, 1e-9)
    exponent = -((value - mean) ** 2) / (2.0 * variance)
    return exp(max(-745.0, exponent)) / sqrt(2.0 * pi * variance)


@dataclass(slots=True)
class _GaussianPosterior:
    mean: float
    variance: float

    def predictive(self, value: float, observation_variance: float) -> float:
        return _normal_pdf(value, self.mean, self.variance + observation_variance)

    def updated(self, value: float, observation_variance: float) -> "_GaussianPosterior":
        precision = 1.0 / self.variance
        observation_precision = 1.0 / observation_variance
        variance = 1.0 / (precision + observation_precision)
        mean = variance * (precision * self.mean + observation_precision * value)
        return _GaussianPosterior(mean, variance)


class BayesianOnlineChangeDetector:
    """Bounded run-length filter returning a Bayesian reset probability.

    A broad prior models a newly started regime and each run-length hypothesis
    carries a Gaussian posterior for the current regime.  Bounding run length
    makes memory constant for an indefinitely running stream.
    """

    def __init__(
        self,
        *,
        hazard: float = 0.035,
        observation_variance: float = 0.012,
        prior_mean: float = 0.08,
        prior_variance: float = 0.20,
        max_run_length: int = 240,
    ) -> None:
        if not 0.0 < hazard < 1.0:
            raise ValueError("hazard must be between 0 and 1")
        self.hazard = hazard
        self.observation_variance = observation_variance
        self.prior = _GaussianPosterior(prior_mean, prior_variance)
        self.max_run_length = max_run_length
        self._probabilities = [1.0]
        self._posteriors = [self.prior]

    def update(self, value: float) -> float:
        value = _clip(value)
        predictive = [
            posterior.predictive(value, self.observation_variance)
            for posterior in self._posteriors
        ]
        continuation = sum(prob * likelihood for prob, likelihood in zip(self._probabilities, predictive))
        reset_likelihood = self.prior.predictive(value, self.observation_variance)
        reset = self.hazard * reset_likelihood
        growth_scale = (1.0 - self.hazard) * continuation
        normalizer = max(reset + growth_scale, 1e-300)
        reset_probability = reset / normalizer

        new_probabilities = [reset / normalizer]
        new_probabilities.extend(
            prob * likelihood * (1.0 - self.hazard) / normalizer
            for prob, likelihood in zip(self._probabilities, predictive)
        )
        new_posteriors = [self.prior.updated(value, self.observation_variance)]
        new_posteriors.extend(
            posterior.updated(value, self.observation_variance)
            for posterior in self._posteriors
        )
        limit = self.max_run_length + 1
        if len(new_probabilities) > limit:
            tail = sum(new_probabilities[limit - 1 :])
            new_probabilities = new_probabilities[: limit - 1] + [tail]
            new_posteriors = new_posteriors[:limit]
        probability_sum = sum(new_probabilities)
        self._probabilities = [value / probability_sum for value in new_probabilities]
        self._posteriors = new_posteriors
        return _clip(reset_probability)

    def reset(self) -> None:
        self._probabilities = [1.0]
        self._posteriors = [self.prior]


@dataclass(slots=True)
class _PendingBoundary:
    boundary_ms: int
    first_seen_ms: int
    confidence: float
    fused_change: float
    reasons: set[str] = field(default_factory=set)
    semantic_similarity: float | None = None


class OnlineHybridSceneSegmenter:
    """Causal CUSUM/BOCPD scene detector with delayed confirmation."""

    def __init__(self, source_id: str, config: OnlineSceneConfig | None = None) -> None:
        self.source_id = source_id
        self.config = config or OnlineSceneConfig()
        self._bayes = BayesianOnlineChangeDetector(
            hazard=self.config.bocpd_hazard,
            observation_variance=self.config.bocpd_observation_variance,
            prior_mean=self.config.bocpd_prior_mean,
            prior_variance=self.config.bocpd_prior_variance,
            max_run_length=self.config.bocpd_max_run_length,
        )
        self._cusum = 0.0
        self._previous_timestamp_ms: int | None = None
        self._scene_start_ms: int | None = None
        self._last_boundary_ms = -10**12
        self._sequence = 1
        self._pending: _PendingBoundary | None = None
        self.last_fused_change = 0.0
        self.last_bocpd_probability = 0.0
        self.last_cusum = 0.0

    def _confirm_pending(self, timestamp_ms: int, *, force: bool = False) -> list[SceneBoundaryEvent]:
        pending = self._pending
        if pending is None:
            return []
        if not force and timestamp_ms - pending.first_seen_ms < self.config.confirmation_ms:
            return []
        semantic_join = (
            pending.semantic_similarity is not None
            and pending.semantic_similarity >= self.config.semantic_join_threshold
            and pending.fused_change < self.config.hard_change_threshold
        )
        self._pending = None
        if semantic_join:
            self._cusum *= 0.25
            return []
        self._sequence += 1
        self._last_boundary_ms = pending.boundary_ms
        self._scene_start_ms = pending.boundary_ms
        event = SceneBoundaryEvent(
            scene_id=f"{self.source_id}-scene-{self._sequence:06d}",
            boundary_ms=pending.boundary_ms,
            emitted_ms=timestamp_ms,
            confidence=pending.confidence,
            reasons=tuple(sorted(pending.reasons)),
            fused_change=pending.fused_change,
        )
        self._cusum = 0.0
        self._bayes.reset()
        return [event]

    def process(self, sample: SceneSample) -> list[SceneBoundaryEvent]:
        if self._previous_timestamp_ms is not None and sample.timestamp_ms < self._previous_timestamp_ms:
            raise ValueError("scene samples must be timestamp ordered")
        if self._scene_start_ms is None:
            self._scene_start_ms = sample.timestamp_ms
        self._previous_timestamp_ms = sample.timestamp_ms

        events = self._confirm_pending(sample.timestamp_ms)
        fused = sample.fused_change(self.config.weights)
        bayes_probability = self._bayes.update(fused)
        self._cusum = max(
            0.0,
            self.config.cusum_decay * self._cusum + fused - self.config.cusum_drift,
        )
        self.last_fused_change = fused
        self.last_bocpd_probability = bayes_probability
        self.last_cusum = self._cusum

        reasons: set[str] = set()
        if self._cusum >= self.config.cusum_threshold:
            reasons.add("cusum")
        if bayes_probability >= self.config.bocpd_threshold:
            reasons.add("bocpd")
        if fused >= self.config.hard_change_threshold:
            reasons.add("hard_change")
        old_enough = sample.timestamp_ms - self._scene_start_ms >= self.config.min_scene_ms
        cooled_down = sample.timestamp_ms - self._last_boundary_ms >= self.config.cooldown_ms
        if reasons and old_enough and cooled_down:
            confidence = max(
                fused,
                _clip(self._cusum / max(self.config.cusum_threshold, 1e-9)),
                bayes_probability,
            )
            if self._pending is None:
                self._pending = _PendingBoundary(
                    boundary_ms=sample.timestamp_ms,
                    first_seen_ms=sample.timestamp_ms,
                    confidence=confidence,
                    fused_change=fused,
                    reasons=reasons,
                    semantic_similarity=sample.semantic_similarity,
                )
            else:
                self._pending.confidence = max(self._pending.confidence, confidence)
                self._pending.fused_change = max(self._pending.fused_change, fused)
                self._pending.reasons.update(reasons)
                if sample.semantic_similarity is not None:
                    self._pending.semantic_similarity = sample.semantic_similarity
        return events

    def finish(self, timestamp_ms: int | None = None) -> list[SceneBoundaryEvent]:
        if self._pending is None:
            return []
        observed = self._previous_timestamp_ms if timestamp_ms is None else timestamp_ms
        assert observed is not None
        return self._confirm_pending(max(observed, self._pending.first_seen_ms), force=True)

    def discontinuity(self, timestamp_ms: int) -> list[SceneBoundaryEvent]:
        events = self.finish(timestamp_ms)
        self._bayes.reset()
        self._cusum = 0.0
        self._pending = None
        self._previous_timestamp_ms = None
        self._scene_start_ms = None
        return events


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions must match")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return _clip(dot / (left_norm * right_norm))


def _ema_vector(
    previous: tuple[float, ...],
    current: tuple[float, ...],
    alpha: float,
) -> tuple[float, ...]:
    return tuple((1.0 - alpha) * old + alpha * new for old, new in zip(previous, current))


@dataclass(frozen=True, slots=True)
class AdaptiveMemoryConfig:
    """Parameters for the non-authoritative adaptive-memory shadow detector."""

    short_alpha: float = 0.42
    long_alpha: float = 0.055
    short_weight: float = 0.42
    long_weight: float = 0.58
    high_threshold: float = 0.24
    low_threshold: float = 0.12
    confirmation_samples: int = 2
    min_scene_ms: int = 3_000
    cooldown_ms: int = 2_000

    def __post_init__(self) -> None:
        probabilities = (
            self.short_alpha,
            self.long_alpha,
            self.short_weight,
            self.long_weight,
            self.high_threshold,
            self.low_threshold,
        )
        if any(value < 0.0 or value > 1.0 for value in probabilities):
            raise ValueError("adaptive-memory values must be in 0..1")
        if self.short_weight + self.long_weight <= 0:
            raise ValueError("adaptive-memory weights must have a positive sum")
        if self.low_threshold > self.high_threshold:
            raise ValueError("low_threshold cannot exceed high_threshold")
        if self.confirmation_samples <= 0:
            raise ValueError("confirmation_samples must be positive")
        if min(self.min_scene_ms, self.cooldown_ms) < 0:
            raise ValueError("adaptive-memory timing values must be non-negative")


@dataclass(frozen=True, slots=True)
class AdaptiveMemorySnapshot:
    timestamp_ms: int
    score: float
    short_similarity: float
    long_similarity: float
    pending: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "score": round(self.score, 6),
            "short_similarity": round(self.short_similarity, 6),
            "long_similarity": round(self.long_similarity, 6),
            "pending": self.pending,
        }


@dataclass(frozen=True, slots=True)
class AdaptiveMemoryBoundary:
    boundary_ms: int
    emitted_ms: int
    score: float
    short_similarity: float
    long_similarity: float

    def as_dict(self) -> dict[str, object]:
        return {
            "boundary_ms": self.boundary_ms,
            "emitted_ms": self.emitted_ms,
            "latency_ms": self.emitted_ms - self.boundary_ms,
            "score": round(self.score, 6),
            "short_similarity": round(self.short_similarity, 6),
            "long_similarity": round(self.long_similarity, 6),
        }


@dataclass(slots=True)
class _AdaptivePending:
    boundary_ms: int
    vectors: list[tuple[float, ...]]
    max_score: float
    short_similarity: float
    long_similarity: float


class AdaptiveMemoryShadowDetector:
    """Causal short/long context memory that never changes production output.

    A candidate freezes both memories and must remain different for a small
    number of samples.  This hysteresis rejects a one-frame flash while keeping
    latency bounded.  On confirmation the new samples seed the next Scene.
    """

    def __init__(self, config: AdaptiveMemoryConfig | None = None) -> None:
        self.config = config or AdaptiveMemoryConfig()
        self._short: tuple[float, ...] | None = None
        self._long: tuple[float, ...] | None = None
        self._pending: _AdaptivePending | None = None
        self._previous_timestamp_ms: int | None = None
        self._scene_start_ms: int | None = None
        self._last_boundary_ms = -10**12
        self.last_snapshot: AdaptiveMemorySnapshot | None = None

    @staticmethod
    def _vector(values: Sequence[float]) -> tuple[float, ...]:
        vector = tuple(float(value) for value in values)
        if not vector:
            raise ValueError("adaptive-memory vectors cannot be empty")
        return vector

    @staticmethod
    def _mean(vectors: Sequence[tuple[float, ...]]) -> tuple[float, ...]:
        return tuple(sum(vector[index] for vector in vectors) / len(vectors) for index in range(len(vectors[0])))

    def _update_memories(self, vector: tuple[float, ...]) -> None:
        assert self._short is not None and self._long is not None
        self._short = _ema_vector(self._short, vector, self.config.short_alpha)
        self._long = _ema_vector(self._long, vector, self.config.long_alpha)

    def process(self, timestamp_ms: int, values: Sequence[float]) -> list[AdaptiveMemoryBoundary]:
        if timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if self._previous_timestamp_ms is not None and timestamp_ms < self._previous_timestamp_ms:
            raise ValueError("adaptive-memory samples must be timestamp ordered")
        vector = self._vector(values)
        if self._short is None:
            self._short = self._long = vector
            self._scene_start_ms = timestamp_ms
            self._previous_timestamp_ms = timestamp_ms
            self.last_snapshot = AdaptiveMemorySnapshot(timestamp_ms, 0.0, 1.0, 1.0, False)
            return []
        if len(vector) != len(self._short):
            raise ValueError("adaptive-memory vector dimensions must match")
        self._previous_timestamp_ms = timestamp_ms
        assert self._long is not None and self._scene_start_ms is not None
        short_similarity = cosine_similarity(vector, self._short)
        long_similarity = cosine_similarity(vector, self._long)
        total_weight = self.config.short_weight + self.config.long_weight
        score = _clip((
            self.config.short_weight * (1.0 - short_similarity)
            + self.config.long_weight * (1.0 - long_similarity)
        ) / total_weight)
        events: list[AdaptiveMemoryBoundary] = []
        if self._pending is not None:
            if score >= self.config.low_threshold:
                self._pending.vectors.append(vector)
                self._pending.max_score = max(self._pending.max_score, score)
                if len(self._pending.vectors) >= self.config.confirmation_samples:
                    pending = self._pending
                    events.append(AdaptiveMemoryBoundary(
                        pending.boundary_ms,
                        timestamp_ms,
                        pending.max_score,
                        pending.short_similarity,
                        pending.long_similarity,
                    ))
                    seed = self._mean(pending.vectors)
                    self._short = self._long = seed
                    self._scene_start_ms = pending.boundary_ms
                    self._last_boundary_ms = pending.boundary_ms
                    self._pending = None
            else:
                self._pending = None
                self._update_memories(vector)
        else:
            old_enough = timestamp_ms - self._scene_start_ms >= self.config.min_scene_ms
            cooled_down = timestamp_ms - self._last_boundary_ms >= self.config.cooldown_ms
            if score >= self.config.high_threshold and old_enough and cooled_down:
                self._pending = _AdaptivePending(
                    timestamp_ms,
                    [vector],
                    score,
                    short_similarity,
                    long_similarity,
                )
                if self.config.confirmation_samples == 1:
                    events.append(AdaptiveMemoryBoundary(
                        timestamp_ms,
                        timestamp_ms,
                        score,
                        short_similarity,
                        long_similarity,
                    ))
                    self._short = self._long = vector
                    self._scene_start_ms = timestamp_ms
                    self._last_boundary_ms = timestamp_ms
                    self._pending = None
            else:
                self._update_memories(vector)
        self.last_snapshot = AdaptiveMemorySnapshot(
            timestamp_ms,
            score,
            short_similarity,
            long_similarity,
            self._pending is not None,
        )
        return events

    def discontinuity(self) -> None:
        self._short = None
        self._long = None
        self._pending = None
        self._previous_timestamp_ms = None
        self._scene_start_ms = None
        self.last_snapshot = None


def _as_vectors(values: Sequence[float | Sequence[float]]) -> list[tuple[float, ...]]:
    vectors: list[tuple[float, ...]] = []
    width: int | None = None
    for value in values:
        vector = (float(value),) if isinstance(value, (int, float)) else tuple(float(item) for item in value)
        if not vector:
            raise ValueError("PELT vectors cannot be empty")
        if width is None:
            width = len(vector)
        elif len(vector) != width:
            raise ValueError("all PELT vectors must have equal dimensions")
        vectors.append(vector)
    return vectors


def pelt_boundaries(
    values: Sequence[float | Sequence[float]],
    *,
    penalty: float,
    min_size: int = 2,
) -> list[int]:
    """Return exact penalised least-squares change indices using PELT pruning.

    Returned indices are starts of new segments in ``1..len(values)-1``.  The
    cost is the sum of within-segment squared errors across all dimensions.
    """

    if penalty < 0:
        raise ValueError("penalty must be non-negative")
    if min_size <= 0:
        raise ValueError("min_size must be positive")
    vectors = _as_vectors(values)
    count = len(vectors)
    if count < 2 * min_size:
        return []
    dimensions = len(vectors[0])
    prefix = [[0.0] * (count + 1) for _ in range(dimensions)]
    squares = [[0.0] * (count + 1) for _ in range(dimensions)]
    for index, vector in enumerate(vectors, start=1):
        for dimension, value in enumerate(vector):
            prefix[dimension][index] = prefix[dimension][index - 1] + value
            squares[dimension][index] = squares[dimension][index - 1] + value * value

    def cost(start: int, end: int) -> float:
        length = end - start
        if length <= 0:
            return 0.0
        total = 0.0
        for dimension in range(dimensions):
            segment_sum = prefix[dimension][end] - prefix[dimension][start]
            segment_squares = squares[dimension][end] - squares[dimension][start]
            total += segment_squares - segment_sum * segment_sum / length
        return max(0.0, total)

    infinity = float("inf")
    best_cost = [infinity] * (count + 1)
    previous = [-1] * (count + 1)
    best_cost[0] = -penalty
    candidates = [0]
    for end in range(min_size, count + 1):
        eligible = [start for start in candidates if end - start >= min_size and best_cost[start] < infinity]
        if not eligible:
            candidates.append(end)
            continue
        scores = [(best_cost[start] + cost(start, end) + penalty, start) for start in eligible]
        value, start = min(scores)
        best_cost[end] = value
        previous[end] = start
        candidates = [
            candidate for candidate in candidates
            if end - candidate < min_size
            or best_cost[candidate] + cost(candidate, end) <= best_cost[end]
        ]
        candidates.append(end)

    boundaries: list[int] = []
    cursor = count
    while previous[cursor] > 0:
        cursor = previous[cursor]
        boundaries.append(cursor)
    boundaries.reverse()
    return boundaries
