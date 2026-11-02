"""FFmpeg-backed non-destructive renderer for clips, fades, captions, text, music and VO."""
from __future__ import annotations
from pathlib import Path
import json
import math
import tempfile
from .runtime import find_executable, run
from .project import Project
from .transcription import caption_cues, make_srt

PRESETS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}


def inspect(source: str) -> dict:
    output = run([find_executable("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                  "-of", "json", source])
    info = json.loads(output)
    video = next((s for s in info.get("streams", []) if s["codec_type"] == "video"), None)
    if not video:
        raise ValueError("No video stream detected")
    duration = float(info.get("format", {}).get("duration", "0") or "0")
    if duration <= 0:
        raise ValueError("Cannot determine a usable video duration")
    return {"duration": duration, "width": video.get("width"), "height": video.get("height"),
            "has_audio": any(s["codec_type"] == "audio" for s in info.get("streams", []))}


def frame_filter(width: int, height: int, framing: str) -> tuple[str, bool]:
    w, h = width, height
    if framing == "crop":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps=30,format=yuv420p", False
    if framing == "pad":
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,format=yuv420p", False
    filter_complex = (f"[0:v]split=2[back][front];"
                      f"[back]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:5[blur];"
                      f"[front]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                      f"[blur][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[vout]")
    return filter_complex, True


def _ffpath(path: str) -> str:
    # FFmpeg filtergraph parsing requires escaping backslash, colon and single quotes.
    return str(Path(path).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace("[", "\\[").replace("]", "\\]")


def overlap_duration(project: Project) -> float:
    if project.transition == "cut" or len(project.clips) <= 1: return 0.
    return min(max(0., project.transition_seconds), max(0., min(x.length for x in project.clips) / 3))


def export_duration(project: Project) -> float:
    return sum(x.length for x in project.clips) - overlap_duration(project) * max(0, len(project.clips) - 1)


def _safe_srt(project: Project, work: Path, transition: float):
    srt_path = work / "generated-captions.srt"
    srt_path.write_text(make_srt(caption_cues(project, transition)), encoding="utf-8-sig")
    return srt_path


def _segment(source: str, dest: Path, project: Project, start: float, duration: float,
             has_audio: bool, report=None, cancel=None):
    w, h = PRESETS[project.aspect]
    vf, complex_filter = frame_filter(w, h, project.framing)
    cmd = [find_executable("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.4f}", "-t", f"{duration:.4f}", "-i", source]
    if not has_audio:
        cmd.extend(["-f", "lavfi", "-t", f"{duration:.4f}", "-i", "anullsrc=r=48000:cl=stereo"])
    if complex_filter:
        cmd.extend(["-filter_complex", vf, "-map", "[vout]"])
    else:
        cmd.extend(["-vf", vf, "-map", "0:v:0"])
    cmd.extend(["-map", "0:a:0?" if has_audio else "1:a:0", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", str(project.export_crf), "-pix_fmt", "yuv420p",
                "-r", "30", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k",
                "-movflags", "+faststart", str(dest)])
    run(cmd, report, cancel=cancel)


def _join(files: list[Path], durations: list[float], out: Path, transition: float, report=None, cancel=None):
    ffmpeg = find_executable("ffmpeg")
    if len(files) == 1:
        # Native copy, no encode loss on intermediate step.
        run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(files[0]), "-c", "copy", str(out)], report, cancel=cancel)
        return
    if transition <= 0:
        playlist = out.parent / "concat.txt"
        playlist.write_text("\n".join("file '" + p.name.replace("'", "'\\''") + "'" for p in files), encoding="utf-8")
        run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", str(playlist), "-c", "copy", str(out)],
            report, cancel=cancel)
        return
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for file in files: cmd.extend(["-i", str(file)])
    filters = []
    vprev, aprev = "0:v", "0:a"
    position = durations[0]
    for i in range(1, len(files)):
        vnew, anew = f"v{i}", f"a{i}"
        # xfade offset is relative to the full concatenated output, not the input.
        filters.append(f"[{vprev}][{i}:v]xfade=transition=fade:duration={transition:.3f}:offset={position-transition:.3f}[{vnew}]")
        filters.append(f"[{aprev}][{i}:a]acrossfade=d={transition:.3f}:c1=tri:c2=tri[{anew}]")
        vprev, aprev = vnew, anew
        position += durations[i] - transition
    cmd.extend(["-filter_complex", ";".join(filters), "-map", f"[{vprev}]", "-map", f"[{aprev}]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    run(cmd, report, cancel=cancel)


def _finalize(project: Project, assembled: Path, output: Path, work: Path, transition: float,
              report=None, cancel=None):
    ffmpeg = find_executable("ffmpeg")
    total = export_duration(project)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(assembled)]
    audio_inputs = []
    for voice in project.voices:
        if Path(voice.path).is_file():
            cmd.extend(["-i", voice.path])
            audio_inputs.append((len(audio_inputs)+1, voice.at, voice.gain, "voice"))
    if project.music_path and Path(project.music_path).is_file():
        cmd.extend(["-stream_loop", "-1", "-i", project.music_path])
        audio_inputs.append((len(audio_inputs)+1, 0, project.music_volume, "music"))
    vfilters = []
    if project.captions and project.segments:
        subtitle = _safe_srt(project, work, transition)
        vfilters.append(f"subtitles='generated-captions.srt':force_style='FontName=Arial,FontSize={int(project.caption_size)},Outline=2,Shadow=1,Alignment=2,MarginV=88'")
    for i, overlay in enumerate(project.overlays):
        if overlay.end <= overlay.start or not overlay.text.strip(): continue
        tf = work / f"overlay-{i}.txt"
        tf.write_text(overlay.text, encoding="utf-8")
        x = "(w-text_w)/2" if overlay.x == "center" else ("w-text_w-60" if overlay.x == "right" else "60")
        y = "65" if overlay.y == "top" else ("(h-text_h)/2" if overlay.y == "middle" else "h-text_h-120")
        vfilters.append(f"drawtext=textfile='overlay-{i}.txt':fontcolor=white:fontsize={int(overlay.size)}:borderw=3:bordercolor=black:x={x}:y={y}:enable='between(t,{overlay.start:.3f},{overlay.end:.3f})'")
    if vfilters:
        cmd.extend(["-vf", ",".join(vfilters)])
    if audio_inputs:
        filters = [f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={max(0.,min(4.,project.source_volume)):.3f}[original]"]
        vo_labels = []
        music_labels = []
        for index, at, gain, kind in audio_inputs:
            label = f"mix{index}"
            delay = max(0, round(at * 1000))
            filters.append(f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={max(0.,min(4.,gain)):.3f},adelay={delay}|{delay},atrim=0:{total:.3f}[{label}]")
            (vo_labels if kind == "voice" else music_labels).append(f"[{label}]")
        original = "[original]"
        if project.duck_original and vo_labels:
            if len(vo_labels) > 1:
                filters.append("".join(vo_labels) + f"amix=inputs={len(vo_labels)}:duration=longest:dropout_transition=0[voicebus]")
                vo_labels = ["[voicebus]"]
            filters.append(f"[original]{vo_labels[0]}sidechaincompress=threshold=0.025:ratio=6:attack=15:release=350[ducked]")
            original = "[ducked]"
        mixing = [original] + vo_labels + music_labels
        filters.append("".join(mixing) + f"amix=inputs={len(mixing)}:duration=first:dropout_transition=0,alimiter=limit=0.96[finalaudio]")
        cmd.extend(["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[finalaudio]"])
    else:
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0"])
    cmd.extend(["-c:v", "libx264" if vfilters else "copy", "-crf", str(project.export_crf),
                "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-t", f"{total:.3f}", "-movflags", "+faststart", str(output)])
    run(cmd, report, cwd=work, cancel=cancel)


def render(project: Project, target: str, report=None, cancel=None):
    report = report or (lambda s: None)
    if not project.video_path or not Path(project.video_path).is_file():
        raise FileNotFoundError("Source video missing")
    if not project.clips:
        raise ValueError("Timeline is empty. Add clips first.")
    if project.aspect not in PRESETS:
        raise ValueError("Unknown aspect ratio")
    media = inspect(project.video_path)
    for clip in project.clips:
        if not (0 <= clip.start < clip.end <= media["duration"] + .1):
            raise ValueError(f"Invalid clip boundaries for {clip.title}")
        if clip.length < .25:
            raise ValueError("Clips must be at least 0.25 seconds")
    output = Path(target).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clipforge-render-") as directory:
        work = Path(directory)
        paths = []
        for index, clip in enumerate(project.clips):
            if cancel and cancel(): raise InterruptedError("Cancelled")
            report(f"Rendering excerpt {index+1}/{len(project.clips)}: {clip.title}")
            dest = work / f"part-{index:04d}.mp4"
            _segment(project.video_path, dest, project, clip.start, clip.length,
                     media["has_audio"], report, cancel)
            paths.append(dest)
        assembled = work / "assembled.mp4"
        transition = overlap_duration(project)
        report("Joining clips and applying transitions…")
        _join(paths, [c.length for c in project.clips], assembled, transition, report, cancel)
        report("Adding captions, text, music, and voiceover…")
        # Render to a staging path, then atomically replace target only on full success.
        staged = work / "finished.mp4"
        _finalize(project, assembled, staged, work, transition, report, cancel)
        if not staged.exists() or staged.stat().st_size < 512:
            raise RuntimeError("Encoder produced no usable output")
        import shutil
        shutil.copyfile(staged, output.with_suffix(output.suffix + ".tmp"))
        output.with_suffix(output.suffix + ".tmp").replace(output)
        subtitle_sidecar = output.with_suffix(".srt")
        subtitle_sidecar.write_text(make_srt(caption_cues(project, transition)), encoding="utf-8-sig")
    report(f"Exported: {output}")
    return output


def render_batch(project: Project, target_directory: str, report=None, cancel=None):
    """Produce one shareable short per timeline clip and an edit-decision manifest."""
    import copy
    import csv
    import re
    root = Path(target_directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not project.clips:
        raise ValueError("Timeline contains no excerpts to batch-export")
    written = []
    report = report or (lambda _: None)
    manifest = []
    for index, clip in enumerate(project.clips, 1):
        if cancel and cancel():
            raise InterruptedError("Batch export cancelled")
        single = copy.deepcopy(project)
        single.clips = [copy.deepcopy(clip)]
        # Compilation voiceover offsets don't map to a standalone short.
        single.voices = []
        single.overlays = []
        if clip.hook.strip():
            from .project import TextOverlay
            single.overlays.append(TextOverlay(clip.hook[:160], 0.15, min(3.2, clip.length - .05), 42, y="top"))
        name = re.sub(r"[^A-Za-z0-9 _-]", "", clip.title).strip().replace(" ", "-")[:48] or "highlight"
        path = root / f"{index:02d}-{name}.mp4"
        report(f"Exporting short {index}/{len(project.clips)}: {clip.title}")
        render(single, str(path), report, cancel)
        written.append(path)
        manifest.append({"file": path.name, "title": clip.title, "start_seconds": clip.start,
                         "end_seconds": clip.end, "category": clip.category,
                         "score": clip.score, "reason": clip.reason, "hook": clip.hook})
    with (root / "clip-manifest.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=manifest[0].keys())
        writer.writeheader(); writer.writerows(manifest)
    return written
