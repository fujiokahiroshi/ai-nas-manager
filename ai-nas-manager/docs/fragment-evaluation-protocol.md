# Fragment detection evaluation protocol

## Objective

Measure the causal Fragment detector separately on edited boundaries, human-rated
important scenes, and consumer recordings.  The same normalized manifest and
metrics are used for every detector version.

## Evaluation set

| Cohort | Target count | Primary purpose |
|---|---:|---|
| TVSum | 5 | Recall of human-rated important two-second intervals |
| SumMe | 5 | Recall of human-selected summary segments |
| ClipShots | 20 | Precision/recall of hard and gradual edit boundaries |
| Smartphone/custom | 3 x 10 min | Audio, subtitle, manual marker, latency and false alarms |

Binary video files and third-party metadata are stored below
`media/evaluation/` and ignored by Git. Only scripts, protocol, normalized
results and reports belong in the repository.

## Metrics

- Boundary precision, recall and F1 with one-to-one matching at +/- 1 second.
- Important-event precision, recall and F1. TVSum uses each video's top 15%
  rated intervals. SumMe uses every annotated reference-summary interval.
  A fixed score threshold can be selected for custom annotations.
- False semantic markers per hour.
- Processing seconds and real-time factor.
- End-to-end live latency and Gemma requests/hour for the custom recordings.

`stream_start`, periodic continuation, quiet timeout and end-of-stream events
are lifecycle events, not semantic markers. They are excluded from important
event scoring.

## Reproduction

Create a normalized TVSum manifest:

```powershell
python scripts/prepare_evaluation_manifest.py `
  --output docs/fragment-evaluation-manifest.json `
  tvsum --info <info.tsv> --annotations <anno.tsv> --video-dir <video-dir> `
  --ids XzYM3PfTM4w 0tmA_C6XwfM 37rzWOQsNIw 91IHQYk1IQM _xMr-HKMfVA
```

Run the detector and score it:

```powershell
python scripts/run_fragment_experiment.py <all manifest videos> --audio --profile legacy-v1 `
  --output docs/fragment-evaluation-predictions.json
python scripts/evaluate_fragment_dataset.py `
  docs/fragment-evaluation-manifest.json `
  docs/fragment-evaluation-predictions.json `
  --output docs/fragment-evaluation-results.json
```

The SumMe subset in this experiment is reconstructed from the ETBench `evs`
annotations derived from SumMe. The five selected videos are `jumps`,
`fire_domino`, `st_maarten_landing`, `scuba`, and `cooking`.

Public-dataset scores must not be interpreted as product accuracy. The custom
recordings are required because public summarization datasets do not represent
the complete video + audio + subtitle + user-marker pipeline.
