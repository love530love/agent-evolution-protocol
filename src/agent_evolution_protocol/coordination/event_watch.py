"""Optional file-event wake bridge; core remains usable without watchdog."""
from __future__ import annotations
from pathlib import Path
from typing import Callable

def available() -> bool:
    try:
        import watchdog  # noqa: F401
    except ImportError:
        return False
    return True

def watch_once(path: Path, callback: Callable[[str], None], *, timeout: float = 1.0) -> bool:
    """Wait briefly for one file event. Returns False when optional dependency is absent."""
    if not available(): return False
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    import threading
    done = threading.Event()
    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory: callback(str(event.src_path)); done.set()
        def on_modified(self, event):
            if not event.is_directory: callback(str(event.src_path)); done.set()
    observer = Observer(); observer.schedule(Handler(), str(Path(path)), recursive=False); observer.start()
    try: return done.wait(timeout)
    finally: observer.stop(); observer.join(timeout=2)
