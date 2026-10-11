"""Paths and channel configuration."""

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"                      # build output, git-ignored
QUEUE = ROOT / "content" / "queue"        # scripts waiting to be produced
PUBLISHED = ROOT / "content" / "published"
REJECTED = ROOT / "content" / "rejected"
DATA = ROOT / "data"
HISTORY = DATA / "history.json"
INSIGHTS = DATA / "insights.md"
PERFORMANCE = DATA / "performance.json"
RESEARCH = WORK / "research.json"


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = Path(path or os.environ.get("AUTOPILOT_CONFIG") or ROOT / "config" / "channel.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def resolution(cfg: dict) -> tuple[int, int]:
    return (1080, 1920) if cfg["format"]["type"] == "short" else (1920, 1080)


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
