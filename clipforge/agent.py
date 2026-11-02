"""Actual local LLM planning, second-pass independent critique, and feedback-aware ranking."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import difflib
import json
import re
import socket
import subprocess
import time
import urllib.request
import urllib.error
from .project import Clip, Project, stamp
from .runtime import find_executable, gguf_path


def parse_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        st = min([x for x in (text.find("["), text.find("{")) if x >= 0], default=-1)
        if st < 0:
            raise ValueError("AI did not return JSON. Check model and try again.")
        dec = json.JSONDecoder()
        return dec.raw_decode(text[st:])[0]


class LocalAI:
    """Communicates ONLY with loopback. Starts bundled llama-server for the GGUF path."""
    def __init__(self, backend: str, model: str = "", ollama_model: str = "qwen2.5:1.5b", report=None):
        self.backend = backend
        self.model = model
        self.ollama_model = ollama_model
        self.proc = None
        self.port = None
        self.report = report or (lambda _: None)

    def __enter__(self):
        if self.backend == "Ollama":
            self._request("http://127.0.0.1:11434/api/tags", None, 8)
            return self
        file = Path(self.model) if self.model else gguf_path()
        if not file.is_file():
            raise FileNotFoundError(f"Local GGUF not found: {file}. Download the model from Tools first.")
        server = find_executable("llama-server")
        # Avoid collisions with an unrelated local service.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        flags = subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0
        self.proc = subprocess.Popen([server, "-m", str(file), "--host", "127.0.0.1", "--port",
                                      str(self.port), "-c", "8192", "-ngl", "0", "--parallel", "1"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server exited while loading the model")
            try:
                resp = self._request(f"http://127.0.0.1:{self.port}/health", None, 2)
                if resp.get("status") == "ok": return self
            except Exception:
                pass
            time.sleep(.6)
        raise TimeoutError("Local GGUF model server did not become ready in 180 seconds")

    def __exit__(self, *_):
        if self.proc:
            self.proc.terminate()
            try: self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired: self.proc.kill()

    def _request(self, url, payload=None, timeout=240):
        raw = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=raw,
                    headers={"Content-Type": "application/json"} if payload else {})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def generate(self, prompt: str, limit: int = 1100) -> str:
        if self.backend == "Ollama":
            response = self._request("http://127.0.0.1:11434/api/chat", {
                "model": self.ollama_model, "stream": False,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0.35, "num_predict": limit}}, 600)
            return response["message"]["content"]
        response = self._request(f"http://127.0.0.1:{self.port}/completion", {
            "prompt": f"<|im_start|>system\nYou are a careful short-form video editor. Respond only with valid JSON.\n<|im_end|>\n<|im_start|>user\n{prompt}\n<|im_end|>\n<|im_start|>assistant\n",
            "n_predict": limit, "temperature": 0.35, "stop": ["<|im_end|>"]}, 600)
        return response["content"]


def windows(project: Project, max_chars=6900):
    """Bound prompts for on-device 8k context while covering the entire source video."""
    parts = []
    cur = []
    length = 0
    for seg in project.segments:
        line = f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text}\n"
        if cur and length + len(line) > max_chars:
            parts.append("".join(cur))
            cur = []
            length = 0
        cur.append(line)
        length += len(line)
    if cur: parts.append("".join(cur))
    return parts


def _extract_candidates(raw, project: Project, text_window: str):
    if isinstance(raw, dict):
        raw = raw.get("clips", raw.get("candidates", []))
    if not isinstance(raw, list): return []
    output = []
    window_match = re.findall(r"\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]", text_window)
    lo = float(window_match[0][0]) if window_match else 0
    hi = float(window_match[-1][1]) if window_match else project.duration
    for item in raw:
        if not isinstance(item, dict): continue
        try:
            start, end = float(item["start"]), float(item["end"])
            if not lo - 1 <= start <= hi or end <= start: continue
            start, end = max(0., start), min(project.duration or hi, hi + 1, end)
            if not 5 <= end - start <= 75: continue
            category = str(item.get("category", "insight")).strip()[:36]
            title = str(item.get("title", "Interesting moment")).strip()[:80]
            hook = str(item.get("hook", "")).strip()[:200]
            reason = str(item.get("reason", "")).strip()[:300]
            score = max(0, min(100, int(float(item.get("score", 60)))))
            # No made-up on-screen quotation: a hook may be a suggestion, not a verbatim quote.
            output.append(Clip(start, end, title, category, score, reason, hook))
        except (TypeError, ValueError, KeyError): continue
    return output


def _overlap(a: Clip, b: Clip) -> float:
    return max(0., min(a.end, b.end) - max(a.start, b.start)) / max(.001, min(a.length, b.length))


def discover(project: Project, backend: str, model: str, ollama_model: str, objective: str,
             report=None, maximum=24):
    """Propose across all windows, run a second critique, apply source grounding and rejection memory."""
    if not project.segments:
        raise ValueError("Transcribe the video first")
    report = report or (lambda _: None)
    candidates = []
    chunks = windows(project)
    with LocalAI(backend, model, ollama_model, report) as llm:
        for i, transcript in enumerate(chunks):
            report(f"AI scouting window {i + 1}/{len(chunks)}…")
            blacklist = "; ".join(project.rejected[-12:])[:700] or "none"
            prompt = f"""You are a professional editor of short viral clips. Find 2-5 COMPLETELY REAL moments in this timestamped transcript. Goal: {objective}.
Important: retain hook, context and payoff, not a random sentence. Select jokes, authentic funny exchanges, surprising insights, practical information, and self-contained stories. 10-55 seconds ideal, 5-75 allowed. Do not fabricate dialogue or imply visual evidence. Prefer diverse moments. Avoid patterns rejected by editor: {blacklist}.
Return ONLY compact JSON array of objects with numeric start, end (EXACT source seconds visible below), title, category, score (0-100), reason (why it works), hook (suggested opening overlay, NOT a quote). No markdown. If no worthwhile clips return [].
TRANSCRIPT:\n{transcript}"""
            try:
                suggestions = _extract_candidates(parse_json(llm.generate(prompt, 1700)), project, transcript)
            except Exception as ex:
                report(f"Window {i+1}: no usable JSON ({ex})")
                continue
            if suggestions:
                report(f"Critiquing {len(suggestions)} suggested clips in window {i+1}…")
                # Deliberately independent evaluation: AI cannot claim it watched video frames.
                summary = [{"index": j, "start": c.start, "end": c.end, "title": c.title,
                            "reason": c.reason} for j, c in enumerate(suggestions)]
                critique = f"""Act as a skeptical second-pass editor. You have TEXT ONLY; do not claim to have seen video frames. For each candidate, decide whether it has a clear premise and payoff *in the actual transcript*. Penalize clickbait and cut-offs. Return ONLY a JSON array of objects {{\"index\":0,\"score\":0-100,\"reason\":\"...\"}}. Do not add/remove candidate indexes.
Candidates: {json.dumps(summary, ensure_ascii=False)}\nTranscript: {transcript[:5500]}"""
                try:
                    results = parse_json(llm.generate(critique, 800))
                    if isinstance(results, dict): results = results.get("scores", [])
                    for x in results:
                        j = int(x.get("index", -1))
                        if 0 <= j < len(suggestions):
                            suggestions[j].score = max(0, min(100, round((suggestions[j].score + float(x.get("score", 50))) / 2)))
                            suggestions[j].reason += " | Review: " + str(x.get("reason", ""))[:140]
                except Exception as ex:
                    report(f"Critique unavailable for window {i+1}: {ex}")
            candidates.extend(suggestions)
    # Filter repeated / already-used moments, compare against actual transcript for each span.
    ranked = []
    for clip in sorted(candidates, key=lambda x: x.score, reverse=True):
        overlapping_text = " ".join(s.text for s in project.segments if s.end > clip.start and s.start < clip.end)
        if len(overlapping_text.strip()) < 30:
            continue
        if any(_overlap(clip, other) > .55 for other in ranked + project.clips):
            continue
        if any(clip.title.lower() == rejected.lower() for rejected in project.rejected):
            continue
        ranked.append(clip)
        if len(ranked) >= maximum: break
    report(f"AI analysis finished: {len(ranked)} candidates (human review required)")
    return ranked
