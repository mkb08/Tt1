"""B-roll from free stock libraries (Pexels, Pixabay), with generated title cards as a fallback."""

import hashlib
import os
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter

from .media import font, hex_to_rgb

PALETTES = [("#0F2027", "#2C5364"), ("#1D2B64", "#5B6FA8"), ("#134E5E", "#71B280"),
            ("#3A1C71", "#D76D77"), ("#232526", "#414345"), ("#0B486B", "#3B8686")]


class StockLibrary:
    """Searches the configured providers, remembers results, and never reuses a clip in one video."""

    def __init__(self, cfg: dict, cache: Path, width: int, height: int):
        self.cfg, self.cache, self.w, self.h = cfg, cache, width, height
        self.portrait = height > width
        self.used: set[str] = set()
        self.results: dict[str, list[dict]] = {}
        self.credits: list[dict] = []
        providers = cfg["visuals"].get("providers", [])
        self.providers = [p for p in providers if os.environ.get(f"{p.upper()}_API_KEY")]
        cache.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def _pexels(self, query: str) -> list[dict]:
        r = requests.get("https://api.pexels.com/videos/search", timeout=30,
                         headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                         params={"query": query, "orientation": "portrait" if self.portrait else "landscape",
                                 "size": "medium", "per_page": 15})
        r.raise_for_status()
        clips = []
        for v in r.json().get("videos", []):
            files = [f for f in v["video_files"] if f.get("file_type") == "video/mp4" and f.get("height")]
            # The smallest file that still fills the frame keeps downloads fast.
            good = [f for f in files if min(f["width"], f["height"]) >= 0.6 * min(self.w, self.h)]
            if not good:
                continue
            best = min(good, key=lambda f: f["width"] * f["height"])
            clips.append({"key": f"pexels-{v['id']}", "url": best["link"], "duration": v.get("duration", 0),
                          "author": v["user"]["name"], "page": v["url"], "provider": "Pexels"})
        return clips

    def _pixabay(self, query: str) -> list[dict]:
        r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                         params={"key": os.environ["PIXABAY_API_KEY"], "q": query[:100],
                                 "per_page": 15, "safesearch": "true"})
        r.raise_for_status()
        clips = []
        for v in r.json().get("hits", []):
            for size in ("medium", "large", "small"):
                f = v["videos"].get(size) or {}
                if f.get("url") and min(f.get("width", 0), f.get("height", 0)) >= 0.5 * min(self.w, self.h):
                    clips.append({"key": f"pixabay-{v['id']}", "url": f["url"], "duration": v.get("duration", 0),
                                  "author": v.get("user", ""), "page": v.get("pageURL", ""), "provider": "Pixabay"})
                    break
        if self.portrait:  # Pixabay has no orientation filter; prefer vertical clips
            clips.sort(key=lambda c: 0 if "vertical" in c["page"] else 1)
        return clips

    def _search(self, query: str) -> list[dict]:
        if query not in self.results:
            found = []
            for provider in self.providers:
                try:
                    found += getattr(self, f"_{provider}")(query)
                except requests.RequestException as exc:
                    print(f"  {provider}: '{query}' failed: {exc}")
            self.results[query] = found
        return self.results[query]

    def _download(self, clip: dict) -> Path:
        path = self.cache / f"{clip['key']}.mp4"
        if not path.exists():
            with requests.get(clip["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                tmp = path.with_suffix(".part")
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                tmp.rename(path)
        return path

    def clips(self, query: str, count: int, fallback_query: str) -> list[Path]:
        """Up to `count` distinct clips for a query, widening the search if needed."""
        words = query.split()
        attempts = [query, " ".join(words[:2]), words[0] if words else "", fallback_query]
        chosen: list[Path] = []
        for q in dict.fromkeys(a for a in attempts if a):
            for clip in self._search(q):
                if len(chosen) == count:
                    return chosen
                if clip["key"] in self.used or clip["duration"] < 2:
                    continue
                try:
                    chosen.append(self._download(clip))
                except requests.RequestException as exc:
                    print(f"  download failed ({clip['key']}): {exc}")
                    continue
                self.used.add(clip["key"])
                self.credits.append({k: clip[k] for k in ("provider", "author", "page")})
        return chosen


def title_card(text: str, index: int, path: Path, width: int, height: int, cfg: dict) -> Path:
    """A gradient card with large text, used when no stock clip is available."""
    top, bottom = (hex_to_rgb(c) for c in PALETTES[index % len(PALETTES)])
    grad = Image.linear_gradient("L").resize((width, height))
    img = Image.composite(Image.new("RGB", (width, height), bottom), Image.new("RGB", (width, height), top), grad)
    glow = Image.new("RGB", (width, height), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([width * 0.1, height * 0.15, width * 0.9, height * 0.55], fill=(255, 255, 255))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(width // 6)), 0.12)
    if text:
        draw = ImageDraw.Draw(img)
        size = int(width * 0.11)
        f = font(cfg["captions"]["font"], size)
        lines = textwrap.wrap(text.upper(), width=14)
        y = height * 0.30 - len(lines) * size * 0.6
        for line in lines:
            w = draw.textlength(line, font=f)
            draw.text(((width - w) / 2, y), line, font=f, fill="white", stroke_width=6, stroke_fill="black")
            y += size * 1.15
    img.save(path)
    return path


def text_overlay(text: str, path: Path, width: int, height: int, cfg: dict) -> Path:
    """Transparent PNG with the scene's key phrase, placed in the upper third."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    size = int(min(width, height) * 0.085)
    f = font(cfg["captions"]["font"], size)
    lines = textwrap.wrap(text.upper(), width=16)
    y = height * (0.16 if height > width else 0.10)
    pad = size * 0.35
    for line in lines:
        w = draw.textlength(line, font=f)
        x = (width - w) / 2
        draw.rounded_rectangle([x - pad, y - pad * 0.4, x + w + pad, y + size * 1.1 + pad * 0.2],
                               radius=int(pad), fill=hex_to_rgb(cfg["captions"]["highlight"]) + (235,))
        draw.text((x, y), line, font=f, fill="black")
        y += size * 1.45
    img.save(path)
    return path


def pick_music(cfg: dict, seed: str) -> Path | None:
    from .config import resolve

    tracks = sorted(resolve(cfg["music"]["dir"]).glob("*.mp3"))
    if not tracks:
        return None
    return tracks[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(tracks)]
