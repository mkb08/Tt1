"""Narration with free Microsoft Edge neural voices (edge-tts), plus word timings for captions."""

import asyncio
import time
from pathlib import Path

from .media import ffmpeg, probe_duration
from .schema import WORDS_PER_SECOND, Script

SCENE_GAP = 0.18   # breath between scenes
END_PAD = 0.6      # let the last word land before the video ends


async def _synthesize(text: str, voice: str, rate: str, out: Path) -> list[dict]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(out, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"word": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return words


def _synthesize_with_retry(text: str, voice: str, rate: str, out: Path) -> list[dict]:
    for attempt in range(4):
        try:
            words = asyncio.run(_synthesize(text, voice, rate, out))
            if out.stat().st_size > 0:
                return words
        except Exception as exc:  # network hiccups from the TTS endpoint are common and transient
            if attempt == 3:
                raise
            print(f"  tts retry after: {exc}")
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("edge-tts returned no audio")


def _offline(text: str, out: Path) -> list[dict]:
    """Silent placeholder audio with estimated timings, for tests and CI without network."""
    tokens = text.split()
    duration = max(len(tokens) / WORDS_PER_SECOND, 1.0)
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{duration:.3f}", "-c:a", "libmp3lame", str(out))
    step = duration / max(len(tokens), 1)
    return [{"word": w.strip(".,!?;:\"'()"), "start": i * step, "end": (i + 0.9) * step} for i, w in enumerate(tokens)]


def narrate(script: Script, cfg: dict, workdir: Path, offline: bool = False) -> tuple[Path, list[float], list[dict]]:
    """Voice each scene separately so scene cuts land exactly on sentence boundaries.

    Returns the narration track, each scene's duration, and absolute word timings.
    """
    voice, rate = cfg["channel"]["voice"], cfg["channel"].get("voice_rate", "+0%")
    durations, words, parts = [], [], []
    clock = 0.0
    for i, scene in enumerate(script.scenes):
        mp3 = workdir / f"voice_{i:02d}.mp3"
        scene_words = _offline(scene.narration, mp3) if offline else _synthesize_with_retry(scene.narration, voice, rate, mp3)
        pad = END_PAD if i == len(script.scenes) - 1 else SCENE_GAP
        wav = workdir / f"voice_{i:02d}.wav"
        ffmpeg("-i", str(mp3), "-af", f"apad=pad_dur={pad}", "-ar", "48000", "-ac", "1", str(wav))
        duration = probe_duration(wav)
        parts.append(wav)
        words += [{**w, "start": w["start"] + clock, "end": w["end"] + clock, "scene": i} for w in scene_words]
        durations.append(duration)
        clock += duration

    listing = workdir / "voice_list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    narration = workdir / "narration.wav"
    ffmpeg("-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", narration.name, cwd=workdir)
    return narration, durations, words
