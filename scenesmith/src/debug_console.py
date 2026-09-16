from __future__ import annotations

import ctypes
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO


class TeeStream:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            try:
                stream.write(text)
                stream.flush()
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self._streams)


def enable_debug_console(log_path: Path | str) -> None:
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_stream = log_file.open("a", encoding="utf-8", buffering=1)

    console_stream = _open_windows_console()
    stdout_targets = [sys.stdout, log_stream]
    stderr_targets = [sys.stderr, log_stream]
    if console_stream is not None:
        stdout_targets.append(console_stream)
        stderr_targets.append(console_stream)

    sys.stdout = TeeStream(*stdout_targets)  # type: ignore[assignment]
    sys.stderr = TeeStream(*stderr_targets)  # type: ignore[assignment]
    print(f"[DEBUG] SceneSmith debug console started {datetime.now().isoformat(timespec='seconds')}")
    print(f"[DEBUG] Debug log: {log_file}")


def _open_windows_console() -> TextIO | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.AllocConsole()
        kernel32.SetConsoleTitleW("SceneSmith Debug Log")
        return open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:
        return None


__all__ = ["enable_debug_console"]
