"""Versioned, editable project data. All times are seconds on the source timeline."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import os
import tempfile


def stamp(seconds: float) -> str:
    seconds = max(0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:06.3f}"


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Clip:
    start: float
    end: float
    title: str = "Untitled highlight"
    category: str = "Highlight"
    score: int = 0
    reason: str = ""
    hook: str = ""

    @property
    def length(self) -> float:
        return max(0., self.end - self.start)


@dataclass
class Voice:
    path: str
    at: float
    gain: float = 1.0
    label: str = "Voiceover"


@dataclass
class TextOverlay:
    text: str
    start: float
    end: float
    size: int = 48
    x: str = "center"
    y: str = "top"


@dataclass
class Project:
    version: int = 1
    video_path: str = ""
    duration: float = 0.0
    segments: list[Segment] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    voices: list[Voice] = field(default_factory=list)
    overlays: list[TextOverlay] = field(default_factory=list)
    music_path: str = ""
    music_volume: float = 0.12
    source_volume: float = 1.0
    duck_original: bool = True
    aspect: str = "9:16"
    framing: str = "blur"
    transition: str = "cut"
    transition_seconds: float = 0.3
    captions: bool = True
    caption_size: int = 20
    transcript_model: str = "small"
    analysis_backend: str = "llama.cpp"
    analysis_model: str = ""
    ollama_model: str = "qwen2.5:1.5b"
    export_crf: int = 21
    project_file: str = ""

    def save(self, path: str | None = None) -> str:
        target = Path(path or self.project_file)
        if not str(target) or str(target) == ".":
            raise ValueError("Choose a project filename")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["project_file"] = str(target.resolve())
        fd, temp_path = tempfile.mkstemp(prefix=".clipforge-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        self.project_file = str(target.resolve())
        return self.project_file

    @classmethod
    def load(cls, filename: str) -> "Project":
        data = json.loads(Path(filename).read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("Unsupported project format version")
        data["segments"] = [Segment(x["start"], x["end"], x["text"],
                                    [Word(**w) for w in x.get("words", [])]) for x in data.get("segments", [])]
        data["clips"] = [Clip(**x) for x in data.get("clips", [])]
        data["voices"] = [Voice(**x) for x in data.get("voices", [])]
        data["overlays"] = [TextOverlay(**x) for x in data.get("overlays", [])]
        allowed = cls.__dataclass_fields__
        project = cls(**{k: v for k, v in data.items() if k in allowed})
        project.project_file = str(Path(filename).resolve())
        return project


def source_words(project: Project, clip: Clip):
    """Extract word timestamps anchored to source, including partially overlapping words."""
    for seg in project.segments:
        if seg.end <= clip.start or seg.start >= clip.end:
            continue
        if seg.words:
            for word in seg.words:
                if word.end > clip.start and word.start < clip.end:
                    yield word
        else:
            yield Word(max(clip.start, seg.start), min(clip.end, seg.end), seg.text)
