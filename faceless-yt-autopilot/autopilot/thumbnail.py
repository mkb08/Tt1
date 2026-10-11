"""1280x720 thumbnail: a frame from the video, darkened, with big bold text."""

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .media import ffmpeg, font, hex_to_rgb
from .schema import Script

W, H = 1280, 720


def make(script: Script, source: Path, out: Path, cfg: dict) -> Path:
    """`source` is a stock clip (a frame is taken from it) or a background image."""
    frame = source
    if source.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        frame = out.with_name("frame.png")
        ffmpeg("-ss", "1.0", "-i", str(source), "-frames:v", "1", str(frame))
    img = ImageOps.fit(Image.open(frame).convert("RGB"), (W, H), centering=(0.5, 0.35))
    img = ImageEnhance.Contrast(ImageEnhance.Brightness(img).enhance(0.75)).enhance(1.15)

    shade = Image.linear_gradient("L").rotate(90, expand=True).resize((W, H))  # 0 at left, 255 at right
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade.point(lambda v: int((255 - v) * 0.75)))

    draw = ImageDraw.Draw(img)
    text = script.thumbnail_text.upper()
    for size in (150, 130, 112, 96, 84):
        f = font(cfg["captions"]["font"], size)
        lines = textwrap.wrap(text, width=max(6, int(10 * 150 / size)))
        if len(lines) <= 3 and max(draw.textlength(l, font=f) for l in lines) <= W * 0.62:
            break
    y = (H - len(lines) * size * 1.08) / 2
    colors = ["#FFFFFF", cfg["captions"]["highlight"], "#FFFFFF"]
    for line, color in zip(lines, colors):
        draw.text((60, y), line, font=f, fill=hex_to_rgb(color), stroke_width=8, stroke_fill="black")
        y += size * 1.08
    img.save(out, "JPEG", quality=90)
    if frame != source:
        frame.unlink(missing_ok=True)
    return out
