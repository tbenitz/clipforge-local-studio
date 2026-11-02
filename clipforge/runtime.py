"""Portable runtime paths, downloaded model management, and process helpers."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

QWEN_NAME = "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
QWEN_URL = "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/" + QWEN_NAME


def app_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def data_root() -> Path:
    root = app_root() / "data"
    try:
        root.mkdir(exist_ok=True, parents=True)
        check = root / ".write-test"
        check.write_text("ok", encoding="utf-8")
        check.unlink()
        return root
    except OSError:
        alternate = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ClipForgeStudio"
        alternate.mkdir(parents=True, exist_ok=True)
        return alternate


def find_executable(name: str) -> str:
    fn = name + (".exe" if os.name == "nt" else "")
    options = [app_root() / "tools" / fn, app_root() / fn]
    for p in options:
        if p.is_file():
            return str(p)
    result = shutil.which(name)
    if result:
        return result
    raise FileNotFoundError(f"{fn} was not found in the portable tools folder or PATH. See README / build instructions.")


def run(command: list[str], progress=None, cwd=None, cancel=None) -> str:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            cwd=cwd, creationflags=flags)
    lines = []
    try:
        for line in proc.stdout:
            if cancel and cancel():
                proc.terminate()
                raise InterruptedError("Operation cancelled")
            if progress:
                progress(line.rstrip())
            lines.append(line)
            if len(lines) > 500:
                lines.pop(0)
        code = proc.wait()
        if code:
            raise RuntimeError(f"Process exited with code {code}:\n{''.join(lines)[-4000:]}")
        return "".join(lines)
    finally:
        if proc.poll() is None:
            proc.kill()


def download(url: str, target: Path, report=None):
    """Atomic streaming download with resume-friendly failure cleanup."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ClipForgeStudio/0.1"})
        with urllib.request.urlopen(req, timeout=45) as response, temp.open("wb") as fp:
            length = int(response.headers.get("Content-Length", "0"))
            received = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                fp.write(chunk)
                received += len(chunk)
                if report:
                    report(f"Downloading {target.name}: " +
                           (f"{100 * received / length:.1f}%" if length else f"{received // 1048576} MiB"))
        if not temp.stat().st_size:
            raise RuntimeError("Empty model download")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def gguf_path() -> Path:
    return data_root() / "models" / QWEN_NAME
