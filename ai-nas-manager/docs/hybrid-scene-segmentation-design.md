# Hybrid scene segmentation

## Why this is separate from Fragment detection

A Fragment is a short item of evidence worth sending to Gemma. A Scene is a
longer semantic interval in which place, people, activity and topic continue.
Several Fragment revisions can therefore belong to one Scene.

## Implemented pipeline

```text
Rockchip MPP/RGA or Windows decoder
  -> current visual/object/audio/subtitle change signals
  -> OnlineMultiSignalFragmenter (evidence selection)
  -> OnlineHybridSceneSegmenter
       -> decayed one-sided CUSUM
       -> bounded Bayesian online run-length/reset filter
       -> 1.5 second confirmation window
       -> semantic-continuity veto
  -> live Scene boundary event
  -> Gemma Scene text and tags
  -> stored multivariate feature sequence
  -> pelt_boundaries() after capture
  -> final boundary revision
```

The live detector never waits for end-of-stream. A boundary records both its
source timestamp and emission timestamp, so user-visible latency is measurable.
The default delay is 1.5 seconds. Memory used by BOCPD is bounded to 240 run
length hypotheses.

## Adapter contract

At each sampled frame, create a `SceneSample` from normalized values in `0..1`:

- `visual_change`: preferably camera-motion compensated;
- `object_change`: detector/tracker population and identity change;
- `audio_change`: environment, speaker or classified event change;
- `subtitle_change`: topic/caption change, not every new caption line;
- `semantic_change`: distance between adjacent lightweight embeddings;
- `semantic_similarity`: similarity to the current Scene, when available.

Call `OnlineHybridSceneSegmenter.process()` in timestamp order. An emitted
`SceneBoundaryEvent` is safe to expose immediately as a provisional boundary.
Store the absolute feature/embedding vector for each sample and call
`pelt_boundaries()` after capture to create the final revision.

## Important behaviour

- `min_scene_ms` and `cooldown_ms` prevent rapid over-segmentation.
- CUSUM catches accumulated moderate evidence.
- BOCPD supplies independent probabilistic evidence for a regime reset.
- high semantic similarity suppresses a candidate unless the change is hard;
- PELT uses exact penalised least-squares segmentation and supports scalar or
  multivariate feature sequences;
- a decoder discontinuity finalizes pending state and resets causal history.

The parameters are initial engineering defaults, not production thresholds.
They must be calibrated independently for edited video, handheld consumer
video, sports and fixed security cameras.

## Reproduction

```powershell
uv run --with pytest pytest tests/test_scene_segmentation.py -q

uv run python scripts/run_fragment_experiment.py `
  media/test_dataset/foreman_qcif.mp4 `
  --ffmpeg C:\path\to\ffmpeg.exe `
  --output docs/scene-fragment-experiment.json
```

`run_fragment_experiment.py` now writes provisional live boundaries and the
post-capture PELT boundaries under each result's `scene_segmentation` object.
PELT currently receives an absolute 16-bin luma-histogram state. The Rockchip
adapter should extend that vector with object, audio and text embeddings without
changing the PELT implementation.

## Multi-Fragment Scene summary

`scene_semantics.py` groups the temporal evidence inside a Scene, compacts only
redundant Fragment revisions, and asks the local LM Studio Gemma model for one
grounded Scene summary. Meaningful early and late revisions are both retained.
It also returns activities, objects, an important change and confidence. A
Scene containing only one distinct Fragment is passed through without another
model call.

```powershell
uv run python scripts/summarize_gemma_scenes.py `
  docs/live-gemma-sandwich-v2-2026-09-10.json `
  --output docs/live-gemma-sandwich-v2-2026-09-10-scenes.json

.\start_pc_app.ps1 `
  -Result "docs/live-gemma-sandwich-v2-2026-09-10-scenes.json"
```

The PC App displays these records with the `Gemma Scene要約` badge. Fragment
text remains the evidence and can still be inspected in the Fragment tab.

## Adaptive Memory shadow mode

`AdaptiveMemoryShadowDetector` runs beside the authoritative CUSUM/BOCPD
detector. It compares every absolute state vector with both a fast short-term
EMA and a slow Scene-level EMA. A high/low hysteresis and consecutive-sample
confirmation reject single-frame flashes. Confirmed candidates seed the next
memory, so memory remains bounded for an indefinitely running Stream.

Shadow decisions never alter Fragment admission, production Scene boundaries,
Gemma requests or database records. Experiment JSON stores them under
`scene_segmentation.shadow_algorithms.adaptive_memory_v1`, including a score
trace and detection latency. The PC App draws confirmed shadow boundaries as
purple markers, allowing direct comparison before enabling the algorithm.
