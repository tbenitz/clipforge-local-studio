"""Portable Windows entrypoint, with launch failure diagnostics that survive a crash."""
from pathlib import Path
import sys
import traceback


def report_crash(exc_type, exc, tb):
    import datetime
    message = "".join(traceback.format_exception(exc_type, exc, tb))
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
    destinations = [root / "data", Path(__import__("os").environ.get("LOCALAPPDATA", str(Path.home()))) / "ClipForgeStudio"]
    log = None
    for directory in destinations:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            log = directory / "startup-error.log"
            with log.open("a", encoding="utf-8") as file:
                file.write(f"\n----- {datetime.datetime.now().isoformat()} -----\n{message}")
            break
        except OSError:
            continue
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            f"ClipForge Studio encountered an error.\n\n{str(exc)[:800]}\n\nDiagnostic log: {log or 'not writable'}",
            "ClipForge Studio", 0x10)
    else:
        sys.stderr.write(message)


def execute():
    if "--self-test" in sys.argv:
        from clipforge.gui import Studio
        from clipforge.runtime import find_executable
        for name in ("ffmpeg", "ffprobe", "llama-server"):
            find_executable(name)
        return 0
    if "--gui-self-test" in sys.argv:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from clipforge.gui import Studio
        app = QApplication(sys.argv)
        window = Studio()
        window.show()
        QTimer.singleShot(1800, app.quit)
        return app.exec()
    from clipforge.gui import main
    return main()


if __name__ == "__main__":
    sys.excepthook = report_crash
    try:
        sys.exit(execute())
    except Exception:
        typ, exc, tb = sys.exc_info()
        report_crash(typ, exc, tb)
        sys.exit(1)
