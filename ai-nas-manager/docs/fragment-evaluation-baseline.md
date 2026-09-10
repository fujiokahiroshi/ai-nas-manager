# Fragment detector baseline

Date: 2026-09-10

Configuration: online-multisignal-v1, 320x180 grayscale, 2 fps, audio enabled,
one-to-one matching with a +/-1000 ms tolerance.

| Dataset | Videos | Duration | Metric | Precision | Recall | F1 | False markers/hour |
|---|---:|---:|---|---:|---:|---:|---:|
| TVSum | 5 | 11.70 min | important interval | 0.113 | 1.000 | 0.204 | 2087.179 |
| SumMe | 5 | 5.36 min | important interval | 0.161 | 0.786 | 0.267 | 1287.289 |
| ClipShots | 20 | 81.41 min | edit boundary | 0.210 | 0.421 | 0.280 | N/A |
| Smartphone/custom | 0/3 | pending | multimodal/product | N/A | N/A | N/A | N/A |

The feature extractor ran at a mean real-time factor of 0.017 to 0.020 across
the three public cohorts, or roughly 50x to 59x faster than playback. This is
offline throughput for low-resolution Fragment detection only; it excludes
Gemma inference, database writes, stream buffering, and UI latency.

## Finding

The current detector is recall-oriented and over-sensitive. It found all 52
selected TVSum intervals and 22 of 28 SumMe intervals, but emitted hundreds of
extra semantic markers. It also found only 85 of 202 ClipShots edit boundaries.
It is therefore a useful candidate generator, but it is not yet accurate enough
to write every candidate directly into the permanent scene database.

The next detector iteration should add hysteresis, signal-specific cooldown,
audio-event classification, and candidate fusion before Gemma. It should then
be rerun against exactly these manifests to measure improvement.

## Scope

This baseline evaluates the Fragment candidate detector, not Gemma caption
quality. Gemma must be evaluated separately on retained fragments for factual
accuracy, temporal consistency, latency, and usefulness of the generated text.

The required three ten-minute smartphone recordings are not present in the
repository. Short `ch01.mp4` through `ch12.mp4` files are 30-second generated
segments and are not substituted for the requested consumer recordings.
