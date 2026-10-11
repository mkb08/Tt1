"""Turn a script into a finished video: voice, b-roll, captions, music, thumbnail, metadata."""

import json
import math
from pathlib import Path

from . import captions, thumbnail, tts
from .config import WORK, resolution
from .media import ffmpeg, filter_path, font_family, font_path, probe_duration
from .schema import Script, slugify
from .visuals import StockLibrary, pick_music, text_overlay, title_card

MAX_SHOT = {"short": 4.0, "long": 7.0}   # cut to a new clip at least this often


def _segment_from_clip(clip: Path, overlay: Path | None, duration: float, out: Path, w: int, h: int, fps: int) -> None:
    clip_len = probe_duration(clip)
    start = 0.5 if clip_len > duration + 1.5 else 0.0
    inputs = ["-ss", f"{start}", "-stream_loop", "-1", "-i", str(clip)]
    chain = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
             f"eq=brightness=-0.04:saturation=1.08")
    if overlay:
        inputs += ["-loop", "1", "-i", str(overlay)]
        graph = f"{chain}[bg];[bg][1:v]overlay=0:0[v]"
    else:
        graph = f"{chain}[v]"
    ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-t", f"{duration:.3f}", "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out))


def _segment_from_card(card: Path, duration: float, out: Path, w: int, h: int, fps: int) -> None:
    zoomed_h = int(h * 1.12) // 2 * 2
    vf = (f"scale=-2:{zoomed_h},crop={w}:{h}:x='(iw-ow)/2':y='(ih-oh)*t/{duration:.3f}',"
          f"setsar=1,fps={fps},format=yuv420p")
    ffmpeg("-loop", "1", "-i", str(card), "-t", f"{duration:.3f}", "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out))


def build_description(script: Script, cfg: dict, credits: list[dict]) -> str:
    clean = lambda s: s.replace("<", "‹").replace(">", "›")
    parts = [script.description.strip()]
    if script.sources:
        parts.append("Sources:\n" + "\n".join(f"• {s.title} ({s.note}) {s.url}" for s in script.sources))
    if credits:
        authors = sorted({f"{c['author']} ({c['provider']})" for c in credits if c["author"]})
        parts.append("Footage: " + ", ".join(authors))
    parts.append(cfg["youtube"].get("description_footer", "").strip())
    hashtags = ["#" + "".join(t.title().split()) for t in script.tags[:3]]
    if cfg["format"]["type"] == "short":
        hashtags.insert(0, "#shorts")
    parts.append(" ".join(hashtags))
    return clean("\n\n".join(p for p in parts if p))[:5000]


def produce(script: Script, script_path: Path, cfg: dict, offline: bool = False) -> Path:
    w, h = resolution(cfg)
    fps = cfg["format"].get("fps", 30)
    slug = slugify(script.topic)
    workdir = WORK / slug
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"Produce: narration ({cfg['channel']['voice']})")
    narration, durations, words = tts.narrate(script, cfg, workdir, offline=offline)
    total = sum(durations)

    library = StockLibrary(cfg, WORK / "stock-cache", w, h)
    if not library.enabled or offline:
        print("Produce: no stock footage keys (or offline); using title cards")
    max_shot = MAX_SHOT[cfg["format"]["type"]]
    segments, thumb_source = [], None
    for i, (scene, duration) in enumerate(zip(script.scenes, durations)):
        shots = max(1, math.ceil(duration / max_shot))
        clips = library.clips(scene.visual_query, shots, cfg["visuals"].get("fallback_query", "nature")) if library.enabled and not offline else []
        overlay = text_overlay(scene.on_screen_text, workdir / f"overlay_{i:02d}.png", w, h, cfg) if scene.on_screen_text else None
        print(f"  scene {i + 1}/{len(script.scenes)}: {duration:.1f}s, {len(clips)} clip(s) for '{scene.visual_query}'")
        if i == 0:  # thumbnail background without the overlay text
            thumb_source = clips[0] if clips else title_card("", 0, workdir / "thumb_bg.png", w, h, cfg)
        if clips:
            shot_len = duration / len(clips)
            for k, clip in enumerate(clips):
                seg = workdir / f"seg_{i:02d}_{k}.mp4"
                _segment_from_clip(clip, overlay if k == 0 else None, shot_len, seg, w, h, fps)
                segments.append(seg)
        else:
            card = title_card(scene.on_screen_text or "", i, workdir / f"card_{i:02d}.png", w, h, cfg)
            seg = workdir / f"seg_{i:02d}_0.mp4"
            _segment_from_card(card, duration, seg, w, h, fps)
            segments.append(seg)

    listing = workdir / "segments.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    ffmpeg("-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", "broll.mp4", cwd=workdir)

    family = font_family(cfg["captions"]["font"])
    (workdir / "captions.ass").write_text(captions.build_ass(words, cfg, w, h, family), encoding="utf-8")
    (workdir / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")

    print("Produce: final mix")
    fontsdir = filter_path(font_path(cfg["captions"]["font"]).parent)
    inputs = ["-i", "broll.mp4", "-i", narration.name]
    video = f"[0:v]tpad=stop_mode=clone:stop_duration=3,ass=captions.ass:fontsdir='{fontsdir}'[v]"
    music = pick_music(cfg, slug)
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        audio = (f"[1:a]asplit=2[voice][key];[2:a]volume={cfg['music']['volume']}[bed];"
                 "[bed][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[ducked];"
                 "[voice][ducked]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        audio = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    final = workdir / "final.mp4"
    ffmpeg(*inputs, "-filter_complex", f"{video};{audio}", "-map", "[v]", "-map", "[a]",
           "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
           "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", final.name, cwd=workdir)

    print("Produce: thumbnail")
    thumb = thumbnail.make(script, thumb_source, workdir / "thumbnail.jpg", cfg)

    meta = {
        "script": str(script_path),
        "slug": slug,
        "title": script.title,
        "description": build_description(script, cfg, library.credits),
        "tags": script.tags,
        "topic": script.topic,
        "source_ids": [s.id for s in script.sources],
        "duration": round(probe_duration(final), 2),
        "video": str(final),
        "thumbnail": str(thumb),
        "offline": offline,
    }
    (workdir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (WORK / "latest.json").write_text(json.dumps({"workdir": str(workdir)}), encoding="utf-8")
    print(f"Produce: done -> {final} ({meta['duration']}s)")
    return workdir
