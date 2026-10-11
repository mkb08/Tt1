"""Publishing history and the script queue. Both live in git so every run sees them."""

import json
import os
import shutil
from pathlib import Path

from .config import HISTORY, PUBLISHED, QUEUE, REJECTED


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    return json.loads(HISTORY.read_text(encoding="utf-8"))


def append_history(entry: dict) -> None:
    history = load_history()
    history.append(entry)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def queued() -> list[Path]:
    return sorted(QUEUE.glob("*.json"))


def archive(script_path: Path, ok: bool, notes: str = "") -> Path:
    """Move a script out of the queue; rejected scripts get a sidecar with the reasons."""
    dest_dir = PUBLISHED if ok else REJECTED
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / script_path.name
    shutil.move(str(script_path), dest)
    if notes:
        dest.with_suffix(".issues.txt").write_text(notes + "\n", encoding="utf-8")
    return dest


def github_output(**values) -> None:
    """Expose values to later GitHub Actions steps (no-op locally)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={str(value).replace(chr(10), ' ')}\n")
