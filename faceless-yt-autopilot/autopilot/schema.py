"""The video script format, shared by Claude (writer) and the production pipeline."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

WORDS_PER_SECOND = 2.6  # edge-tts neural voices at a slightly raised rate


class Source(BaseModel):
    id: str = Field(description="Source id exactly as given in the research file, e.g. 'PMID:40123456'")
    title: str
    url: str
    note: str = Field(description="Study type, population and size, e.g. 'RCT, 312 adults with insomnia, 12 weeks'")


class Scene(BaseModel):
    narration: str = Field(description="What the narrator says in this scene: 1-3 short, spoken-style sentences")
    visual_query: str = Field(description="2-4 word stock-footage search, concrete and filmable, e.g. 'woman jogging sunrise'")
    on_screen_text: str = Field(description="Optional 1-5 word overlay for the key number or idea; empty string for none")
    source_ids: list[str] = Field(description="Ids of the sources backing factual claims in this scene; empty for hook/CTA lines with no claim")


class Script(BaseModel):
    topic: str = Field(description="Short topic label used to avoid repeats, e.g. 'post-meal walks and blood sugar'")
    title: str = Field(description="YouTube title, under 70 characters, curiosity-driven but accurate")
    description: str = Field(description="YouTube description: 2-4 sentence summary, then key takeaways. Do not list sources; they are appended automatically")
    tags: list[str] = Field(description="8-15 search tags")
    thumbnail_text: str = Field(description="2-4 punchy words for the thumbnail")
    scenes: list[Scene]
    sources: list[Source]


def load_script(path: str | Path) -> Script:
    return Script.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_script(script: Script, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(script.model_dump_json(indent=2) + "\n", encoding="utf-8")


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "video"


def word_count(script: Script) -> int:
    return sum(len(s.narration.split()) for s in script.scenes)


def check_script(script: Script, cfg: dict, research_ids: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block production; warnings are logged."""
    errors: list[str] = []
    warnings: list[str] = []

    if not script.title.strip() or len(script.title) > 100:
        errors.append(f"title must be 1-100 characters (got {len(script.title)})")
    for field in ("title", "description"):
        if "<" in getattr(script, field) or ">" in getattr(script, field):
            errors.append(f"{field} contains '<' or '>', which YouTube rejects")
    if len(script.description) > 4500:
        errors.append("description is too long (keep under 4500 characters; sources are appended)")
    if sum(len(t) + 2 for t in script.tags) > 480:
        errors.append("tags exceed YouTube's 500 character limit")
    if not 3 <= len(script.scenes) <= 40:
        errors.append(f"expected 3-40 scenes (got {len(script.scenes)})")
    for i, scene in enumerate(script.scenes):
        if not scene.narration.strip():
            errors.append(f"scene {i + 1} has no narration")
        if not scene.visual_query.strip():
            errors.append(f"scene {i + 1} has no visual_query")

    source_ids = {s.id for s in script.sources}
    for i, scene in enumerate(script.scenes):
        for sid in scene.source_ids:
            if sid not in source_ids:
                errors.append(f"scene {i + 1} cites {sid}, which is not in sources")
    if cfg["writer"].get("require_sources", True):
        if not script.sources:
            errors.append("require_sources is on but the script has no sources")
        if not any(scene.source_ids for scene in script.scenes):
            errors.append("require_sources is on but no scene cites a source")
        if research_ids is not None:
            for sid in source_ids - research_ids:
                errors.append(f"source {sid} was not in the research file (possible invented citation)")

    target = cfg["format"]["target_seconds"]
    estimate = word_count(script) / WORDS_PER_SECOND
    if cfg["format"]["type"] == "short" and estimate > 175:
        errors.append(f"narration is ~{estimate:.0f}s; Shorts must stay under 3 minutes")
    if not 0.6 * target <= estimate <= 1.4 * target:
        warnings.append(f"narration is ~{estimate:.0f}s against a {target}s target")
    return errors, warnings


def research_ids(research_path: Path) -> set[str] | None:
    if not research_path.exists():
        return None
    data = json.loads(research_path.read_text(encoding="utf-8"))
    return {item["id"] for item in data.get("evidence", [])}
