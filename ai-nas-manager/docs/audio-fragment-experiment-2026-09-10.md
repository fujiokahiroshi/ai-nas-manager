# Audio-assisted Fragment experiment (2026-09-10)

## Result

A 10-second static video was generated with these audio events:

- 2.0–3.0 s: moderate 440 Hz sound;
- 6.0–6.25 s: strong 1,200 Hz impact-like sound.

The exact same video was analyzed at 2 fps in two conditions.

| Input signals | Fragments | Updates | Important result |
|---|---:|---:|---|
| Visual only | 1 | 0 | Static picture produced no event update |
| Visual + audio | 2 | 1 | Audio updated at 2.0 s and opened at 6.0 s |

The audio-assisted event sequence was:

```text
0.0 s OPEN   stream_start
2.0 s UPDATE audio_change=1.0
5.0 s CLOSE  quiet_timeout
6.0 s OPEN   audio_change=1.0
8.5 s CLOSE  quiet_timeout
```

This proves that an important sound can mark a Fragment even when every video
frame is visually unchanged.

## Algorithm

FFmpeg decodes only the first audio stream to 16 kHz mono signed PCM. A causal
500 ms window calculates:

- RMS level in dBFS;
- peak amplitude;
- zero-crossing rate;
- normalized spectral flux;
- silent/active transition;
- rising-level onset.

These values produce `audio_change` in the range 0–1. Changes below the silence
floor are suppressed, and falling edges are deliberately weaker than rising
edges. An `audio_change` of at least 0.72 can independently open or update a
Fragment.

This stage performs no speech recognition and sends no audio to a cloud.

## Reproduction

```powershell
python scripts/generate_audio_event_sample.py --ffmpeg C:\path\to\ffmpeg.exe

python scripts/run_fragment_experiment.py `
  media/test_dataset/audio_event_sample.mp4 `
  --ffmpeg C:\path\to\ffmpeg.exe `
  --output docs/audio-fragment-visual-only.json

python scripts/run_fragment_experiment.py `
  media/test_dataset/audio_event_sample.mp4 `
  --ffmpeg C:\path\to\ffmpeg.exe `
  --audio `
  --output docs/audio-fragment-multisignal.json
```

The generated MP4 is ignored by Git. The generator and result JSON files make
the experiment reproducible without committing binary media.

## Next step

For live camera/RTSP input, audio windows must be read concurrently with video
and aligned on the same monotonic timeline. The DSP interface remains the same.
Later adapters can add local VAD, sound classification, and speech-to-text.

