# ClipForge Studio

**Local-first transcription, two-pass AI clip scouting and an editable short-form video studio for Windows.** A portable `.exe` lives inside a self-contained folder; it installs no Python on the destination PC. The executable is not available until the GitHub Actions Windows build succeeds.

![platform](https://img.shields.io/badge/Windows-10%2F11-2962d9) ![AI](https://img.shields.io/badge/inference-local--only-40b396) ![render](https://img.shields.io/badge/FFmpeg-H.264-9a6bea)

## Quick start (non-developer)

1. In **Actions → Build portable Windows EXE → Run workflow**, start a build on `main`.
2. Open the successful run and download **ClipForgeStudio-Windows-Portable** from its **Artifacts** section. GitHub may download an outer artifact ZIP; extract that first, then extract `ClipForgeStudio-Windows-Portable.zip`.
3. Open `ClipForgeStudio/ClipForgeStudio.exe` on a Windows 10/11 x64 machine. **Keep the whole folder together**, including `_internal`, `tools` and `data`. You can move the entire folder onto a USB drive. No installer or admin rights are intended to be required.
4. Import a video → **Transcribe locally** (Whisper `small` by default; first use downloads its weights).
5. Under Settings, **Download local Qwen 1.5B** (≈1 GB) once, or choose **Ollama** if you already run Ollama locally with the selected model.
6. **AI Picks → Discover highlights**. The model proposes highlights and performs a second critical pass against the transcript. Preview, accept or reject candidates; review them in the Timeline tab.
7. Add text overlays, record or generate voiceovers, choose a soundtrack, and choose output format/captions/transitions.
8. **Export MP4** for one montage, or **Batch Shorts** for separate MP4s for every accepted clip. Caption `.srt` sidecars are also written. Batch exports include a CSV manifest of the scores and source timestamps.

**Offline/privacy:** All transcription, analysis, project files, renders, microphone audio and video stay local. The only model network traffic is explicitly initiated downloads or Whisper's first-use weight download. Inference talks only to `127.0.0.1` for llama.cpp or an existing local Ollama daemon. Neither sends video to a third-party API. Once binaries/weights are available locally, disconnect the network.

## Working features in v0.1

| Area | Functionality |
|---|---|
| Import | MP4, MKV, MOV, WebM, AVI, M4V, TS; FFprobe duration/stream validation |
| Review | Embedded video player, seek ±5 s, scrub, mark IN/OUT, source timecode |
| Transcription | `faster-whisper`, model selection (tiny → large), CPU INT8, automatic language, word timestamps, searchable transcript, SRT export |
| Agentic clip scouting | Offline Qwen GGUF using bundled CPU `llama-server`, or existing local Ollama; transcript windows cover whole recording; proposes jokes, insights, information, story payoffs; separate critic pass; ranked candidates, transcript-bound timings, rejection memory |
| Editor | Non-destructive source trim, split, reorder, reject, remove, undo up to 40 edits, multiple accepted clips, timeline multi-lane overview with playhead |
| Graphics | Timed top/middle/bottom text overlays with font size control; title/hook animations are basic (not motion graphics) |
| Captions | Auto-generated word-group subtitles, FFmpeg/libass burn-in, SRT sidecar, style/font-size setting |
| Aspect ratios | 9:16, 16:9, 1:1, 4:5; crop, black padding, blurred-background framing |
| Transitions | Straight cuts or video crossfades paired with audio crossfades |
| Voiceover | Microphone recording, audio import and Windows SAPI offline TTS; multiple VO clips positioned on final timeline; gain |
| Audio | Mix existing soundtrack, imported voiceovers and looping background music; adjustable original volume; voice-triggered audio ducking; mix limiter; music volume |
| Export | H.264/AAC MP4; individual short batch export + CSV edit-decision list; quality controls; temporary staging and atomic output replacement |
| Projects | Save/load JSON `.clipforge.json` projects; editable clip boundaries and transcript corrections; periodic unsaved-project recovery; model data stored beside app when writable |
| Delivery | GitHub Actions builds and packages onedir Windows portable executable; upload artifact; tag-based GitHub releases |

**Accuracy limits:** The AI reads *speech transcript*, not visual frames; it cannot reliably detect funny expressions, physical gags, facial reactions or visual-only context. All clip scores and hook suggestions are subjective; there is no guarantee of viral reach. Transcript/word timings should be checked before publishing. Rejection feedback influences future prompts; it does **not** retrain neural-network weights.

### Requirements / performance

- Windows 10/11 x64 with ~8 GB RAM minimum for `Whisper small` + Qwen 1.5B CPU; 16 GB recommended for long videos and larger models. Windows OS supports the build; the **GitHub workflow**, not a Chromebook, compiles the EXE.
- Sufficient free disk: app distribution hundreds of MB, local Qwen model ≈1 GB, Whisper small weights hundreds of MB, **temporary full-resolution video renders can consume many GB**, and source/output video files may be large.
- NVIDIA GPU inference acceleration is not configured in this initial portable CPU build. Video encoding uses libx264 CPU. Larger Whisper models can be slow on CPU.
- Full local operation after model weight downloads. The desktop app is designed for Windows and does not run as a Chromebook EXE. Whisper is **not bundled in the app ZIP**; the GGUF model is optional and is also **not bundled**.
- Media playback preview support depends on Qt's available Windows codecs. Export uses its own FFmpeg binaries and can handle more formats than the preview player.

## Run from source (Windows developer)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-build.txt
python main.py
```

Place `ffmpeg.exe`, `ffprobe.exe`, `llama-server.exe` and llama.cpp's companion DLLs under `tools/`, or have these command-line tools in PATH. The GitHub workflow bundles them automatically; no external Python interpreter is required for its portable EXE.

`python -m pytest -q` runs unit tests and a real MP4 render integration test when FFmpeg is present. Build workflow does **not** bundle model weights or upload user footage.

## Editor walkthrough

**Timeline:** Import → mark IN/OUT in source video → `Add selected range`, or accept AI-picked segments. Split one selected clip at playhead. Edit IN and OUT in seconds directly, or use up/down to rearrange; project edits are non-destructive. The visual overview has separate video, voice, music and title lanes; dragging clip blocks to move them is not implemented in v0.1 (use up/down row controls).

**Voiceover:** Under Audio/VO, set offset on the *final* edited timeline, type a script and `Generate offline voice` using installed Windows voices, or start/stop a microphone recording. You can also import WAV, MP3 etc. The default audio mix lowers original audio when narration plays (sidechain ducking), then adds voiceover and music. Turn ducking off in Audio/VO if it sounds unnatural.

**Captions:** Choose Settings → Burn captions. The caption generator groups Whisper word timestamps into short blocks. Output is an `.mp4` and `.srt` adjacent to it; source transcript SRT can be exported separately. If you edit a transcript segment, the editor retains its segment timestamp but discards now-invalid word timing. You can also manually edit the SRT sidecar after export.

**Model alternatives:** Set the custom GGUF field to any suitable llama.cpp-supported GGUF; larger models often improve timing and clip choice at the expense of RAM/speed. Or choose Ollama and enter the name of a model you have already pulled locally, such as `qwen2.5:1.5b`. This is a *local* Ollama daemon, not a remote API.

**Model storage:** The portable folder's `data/` directory contains local weights and recordings when writable. On read-only media, data falls back to `%LOCALAPPDATA%\ClipForgeStudio`. Do not run two instances editing the same project simultaneously.

## Limitations and next-stage roadmap

This is a functional **first version**, not a replacement yet for DaVinci Resolve/Premiere. A truly full professional suite would require additional work on visual-semantic frame analysis, waveform/keyframe editing, multiple overlapping visual tracks, transitions library, motion graphics, face-aware 9:16 reframing, pan-and-scan tracking, undo history across restarts, proxy editing of very large files, automatic audio ducking/noise cleanup, robust crash recovery and GPU hardware encoding. These are **not** advertised as implemented.

### Priority roadmap

1. Frame-aware on-device visual understanding to catch facial jokes, gestures, reactions and cutaways that transcript-only selection misses, with a shot-level preview review pass.
2. Dedicated multi-track drag-and-drop timeline with keyframed positions, crop and speed curves, waveform peaks, native crop handles and trim handles.
3. Local sound cleanup, silence detection, beat sync; LUTs/color grading and integrated effects presets.
4. Speech-aware speaker diarization, dynamic karaoke captions, multi-language dubbing and better offline TTS choices.
5. Project-relative media paths, on-screen real-time output preview, user-controlled proxy generation and GPU acceleration where hardware allows.

## Trust and safety

Use footage/music you own or are authorized to edit. Keep direct quoted subtitles faithful. Critically review AI captions/hooks to avoid misrepresenting speakers, changing their meaning through edits, or inventing statements. See [THIRD_PARTY.md](THIRD_PARTY.md) for bundled FFmpeg GPL and other library licensing; the assembled build does not become MIT-only just because this project's Python code is MIT licensed.

## License

Original project Python code: MIT (see LICENSE). Bundled dependencies: their own licenses, especially FFmpeg GPL binaries.
