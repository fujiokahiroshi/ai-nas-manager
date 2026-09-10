# Vision experiment models

Large model files are downloaded locally and ignored by Git.

## YOLOX-Tiny ONNX

- Upstream: `Megvii-BaseDetection/YOLOX`
- Release URL: <https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx>
- Local filename: `models/yolox_tiny.onnx`
- Size: 20,219,662 bytes
- SHA-256: `427CC366D34E27FF7A03E2899B5E3671425C262EA2291F88BB942BC1CC70B0F7`
- Input: 416x416

Download and verify in PowerShell:

```powershell
curl.exe -L --fail -o models/yolox_tiny.onnx `
  https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx
Get-FileHash -Algorithm SHA256 models/yolox_tiny.onnx
```

Run the experiment:

```powershell
uv run --with-requirements requirements-vision-experiment.txt python `
  scripts/run_object_fragment_experiment.py `
  media/test_dataset/pedestrian_area_1080p25.mp4 `
  --model models/yolox_tiny.onnx `
  --output docs/object-fragment-experiment-2026-09-10.json
```

The Windows ONNX adapter is an experiment.  RK3588 production code should use
an RKNN-converted detector and emit the same normalized `Detection` records.

