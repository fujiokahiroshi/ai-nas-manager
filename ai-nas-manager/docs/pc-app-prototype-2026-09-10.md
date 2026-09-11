# AI NAS Manager PC prototype

## Purpose

This prototype turns the existing video-plus-Gemma experiment output into a
local, editable video database. It demonstrates the PC product shape before the
input layer is migrated to Rockchip MPP/RGA.

## Included functions

- Local MP4 playback with HTTP byte-range seeking.
- Gemma text, timestamp, detected objects, confidence, and thumbnail displayed
  together.
- Clicking a card or timeline marker seeks and plays the corresponding scene.
- Full-text filtering across Gemma text, user text, action, and object names.
- Favorite filtering for highlight selection.
- User-edited descriptions stored separately from the original Gemma result.
- SQLite persistence across application restarts and JSON re-imports.
- Localhost-only binding by default; the video is not uploaded to a cloud.
- Native Windows video selection that bypasses Edge's mobile-upload chooser.
- Static analysis runs the Fragment/Gemma/Scene pipeline without source-time
  waiting; real-time analysis follows the source timestamps and publishes
  Fragment text while the video continues.
- The analysis menu reports progress, current phase, failures, and completion.

## Start

From PowerShell in the `ai-nas-manager` directory:

```powershell
.\start_pc_app.ps1
```

The default browser opens `http://127.0.0.1:8788`. Stop the application with
`Ctrl+C` in PowerShell.

To use another live experiment result:

```powershell
.\start_pc_app.ps1 -Result docs\another-gemma-result.json -Port 8788
```

The result JSON must contain `source`, whose video file must still exist, and an
`inferences` array in the format produced by `run_live_gemma_experiment.py`.

## Analyze a selected video

Start LM Studio's local server on port `1234` with `gemma4-12b-qat` loaded.
In the PC App, open **Menu**, select **Windowsから映像を開く…**, then choose:

- **静的解析を開始** for fastest possible file analysis.
- **リアルタイム解析を開始** to pace decoding to the video timestamps and
  show new Fragment text during playback.

Both modes use the same object-assisted Fragment detector and hybrid Scene
segmentation. After Fragment processing, Gemma produces the multi-Fragment
Scene summaries. Intermediate JSON is kept under `media/pc-app-analysis/` and
is excluded from Git.

## Persistence model

The default database is `pc_app.sqlite3` and is ignored by Git. The stable key
is `fragment_id + revision`. Importing a newer copy updates machine-generated
fields but preserves `user_text` and `favorite`, allowing Edge-generated text
to be customized without destroying its provenance.

## Verification result

- UI endpoint: HTTP 200.
- Fragment API: six existing Gemma revisions returned.
- Thumbnail endpoint: HTTP 200.
- Video range request: HTTP 206 with the requested 1,024 bytes.
- Static end-to-end check: 3 Fragment records and 2 Scenes.
- Real-time end-to-end check: 5 Fragment revisions and 2 Scenes, with records
  visible before completion.

## Next product increments

1. Feed live RTSP fragment revisions into the same database through an event
   queue instead of importing a completed JSON file.
2. Add media-library selection and multiple source records.
3. Add start/end ranges and export selected favorites as a highlight video.
4. Package the localhost service and UI as a Windows desktop executable.
