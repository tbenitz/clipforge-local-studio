"""Local Whisper transcription. Timestamps stay relative to original source media."""
from __future__ import annotations
from pathlib import Path
import tempfile
import re
from .project import Segment, Word, stamp
from .runtime import find_executable, data_root, run

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"]


def transcribe(source: str, model_name: str = "small", language: str = "auto", report=None):
    # Important: import lazily so a missing inference binary does not prevent opening the editor.
    from faster_whisper import WhisperModel
    models_dir = data_root() / "models" / "whisper"
    models_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clipforge-audio-") as scratch:
        audio = str(Path(scratch) / "source.wav")
        if report: report("Extracting 16 kHz mono audio…")
        run([find_executable("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", source, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", audio])
        if report: report(f"Loading Whisper {model_name} (downloads on first use)…")
        engine = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(models_dir))
        iterator, info = engine.transcribe(audio, language=None if language == "auto" else language,
                                          word_timestamps=True, beam_size=5, vad_filter=False,
                                          condition_on_previous_text=False, hallucination_silence_threshold=None)
        output = []
        for index, seg in enumerate(iterator):
            words = [Word(float(w.start), float(w.end), w.word.strip())
                     for w in (seg.words or []) if w.start is not None and w.end is not None]
            output.append(Segment(float(seg.start), float(seg.end), seg.text.strip(), words))
            if report: report(f"Transcribed {stamp(seg.end)} · {len(output)} segments · {info.language}")
        if not output:
            raise RuntimeError("No speech detected. Check whether the source contains an audio track.")
        return output, info.language


def srt_timestamp(t: float) -> str:
    ms = max(0, round(t * 1000))
    hh, rem = divmod(ms, 3600000)
    mm, rem = divmod(rem, 60000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02}:{mm:02}:{ss:02},{ms:03}"


def make_srt(items: list[tuple[float, float, str]]) -> str:
    lines = []
    for i, (start, end, text) in enumerate(items, 1):
        if end <= start or not text.strip():
            continue
        text = re.sub(r"\s+", " ", text.strip())
        lines.append(f"{i}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n")
    return "\n".join(lines)


def transcript_srt(segments: list[Segment]) -> str:
    return make_srt([(s.start, s.end, s.text) for s in segments])


def caption_cues(project, transition_seconds: float = 0.):
    """Output-timeline word groups. Handles cuts and overlap for crossfades."""
    cues = []
    out_at = 0.
    for idx, clip in enumerate(project.clips):
        if idx:
            out_at -= transition_seconds
        word_group = []
        from .project import source_words
        for word in source_words(project, clip):
            local_start = max(0., word.start - clip.start)
            local_end = min(clip.length, word.end - clip.start)
            start, end = out_at + local_start, out_at + local_end
            if end <= start: continue
            if word_group and (len(word_group) >= 5 or end - word_group[0][0] > 2.4 or start - word_group[-1][1] > 0.5):
                cues.append((word_group[0][0], word_group[-1][1], " ".join(w[2] for w in word_group)))
                word_group = []
            word_group.append((start, end, word.text))
        if word_group:
            cues.append((word_group[0][0], word_group[-1][1], " ".join(w[2] for w in word_group)))
        out_at += clip.length
    # Clamp overlapping groups so screen doesn't show two separate lines simultaneously.
    clipped = []
    for start, end, txt in sorted(cues):
        if clipped and start < clipped[-1][1]:
            old = clipped[-1]
            clipped[-1] = (old[0], max(old[0] + .08, start), old[2])
        if end > start + .04:
            clipped.append((start, end, txt))
    return clipped
