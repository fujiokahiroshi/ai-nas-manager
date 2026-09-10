# Fragment detector v2 evaluation

Date: 2026-09-10

All runs use the same public videos, 320x180 luma at 2 fps, audio enabled, and
+/-1000 ms one-to-one matching as the legacy-v1 baseline.

## Balanced profile

| Dataset | Task | Version | Precision | Recall | F1 | False markers/hour |
|---|---|---|---:|---:|---:|---:|
| TVSum (5) | important interval | legacy-v1 | 0.113 | 1.000 | 0.204 | 2087.179 |
| TVSum (5) | important interval | fused-v2-balanced | 0.254 | 0.615 | 0.360 | 482.051 |
| SumMe (5) | important interval | legacy-v1 | 0.161 | 0.786 | 0.267 | 1287.289 |
| SumMe (5) | important interval | fused-v2-balanced | 0.273 | 0.429 | 0.333 | 358.202 |
| ClipShots (20) | edit boundary | legacy-v1 | 0.210 | 0.421 | 0.280 | N/A |
| ClipShots (20) | edit boundary | fused-v2-balanced | 0.219 | 0.431 | 0.290 | N/A |

TVSum false markers fell 76.9%, and SumMe false markers fell 72.2%. F1 improved
on all three cohorts, but important-scene recall fell. The balanced profile is
therefore suitable for the Gemma request queue, not as the sole source of truth.

## Conservative profile

The conservative profile reduced false markers further, to 394.872/hour on
TVSum and 279.846/hour on SumMe, but recall fell to 0.519 and 0.321. The balanced
profile has the better F1 and is the default recommendation for the next live
experiment.

## Database admission

Detection now produces transient candidates. Permanent storage is a separate
decision:

1. Manual markers are always confirmed.
2. Automatic results need a meaningful Gemma observation plus structured
   action/change/object evidence.
3. Gemma confidence contributes to the score but is not accepted by itself.
4. Fused change, impact sound, and subtitle triggers add corroborating evidence.
5. Review and candidate states remain outside the permanent database unless
   the PC app is started with `--include-unconfirmed`.

This prevents every detector pulse from becoming a permanent scene record while
preserving a bounded candidate path for later model improvements.

## Remaining validation

The three requested ten-minute smartphone recordings are still required. They
must validate camera shake, conversation suppression, clap/impact recall,
subtitle triggers, manual-marker latency, Gemma request rate, and confirmed
database precision under consumer conditions.
