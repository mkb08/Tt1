"""Thin wrappers around ffmpeg/ffprobe and fonts."""

import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .config import resolve

FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def ffmpeg(*args: str, cwd: Path | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


@lru_cache
def font_path(configured: str) -> Path:
    for candidate in [resolve(configured), *map(Path, FALLBACK_FONTS)]:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No usable font found (configured: {configured})")


def font(configured: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path(configured)), size)


def font_family(configured: str) -> str:
    return font(configured, 20).getname()[0]


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
