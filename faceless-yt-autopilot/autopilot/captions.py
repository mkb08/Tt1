"""Word-by-word "karaoke" captions as an ASS subtitle file, burned in by ffmpeg."""

PAUSE_BREAK = 0.22   # a pause this long (a comma or full stop) starts a new caption
HOLD = 0.25          # keep the last caption up briefly after the final word


def _ts(seconds: float) -> str:
    cs = max(int(round(seconds * 100)), 0)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    c = hex_color.lstrip("#")
    return f"&H{alpha:02X}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _inline(hex_color: str) -> str:
    c = hex_color.lstrip("#")
    return f"&H{c[4:6]}{c[2:4]}{c[0:2]}&".upper()


def chunk_words(words: list[dict], per_caption: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    for w in words:
        current = chunks[-1] if chunks else None
        if (current is None or len(current) >= per_caption or w.get("scene") != current[-1].get("scene")
                or w["start"] - current[-1]["end"] > PAUSE_BREAK):
            chunks.append([w])
        else:
            current.append(w)
    return chunks


def build_ass(words: list[dict], cfg: dict, width: int, height: int, font_family: str) -> str:
    cc = cfg["captions"]
    portrait = height > width
    size = int(cc["font_size"] * (width / 1080 if portrait else height / 1080 * 0.85))
    margin_v = int(height * (0.28 if portrait else 0.07))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_family},{size},{_ass_color(cc['color'])},{_ass_color(cc['color'])},{_ass_color(cc['outline'])},{_ass_color('#000000', 0x80)},0,0,0,0,100,100,1,0,1,{max(size // 14, 3)},3,2,{width // 12},{width // 12},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    base, hi = _inline(cc["color"]), _inline(cc["highlight"])
    chunks = chunk_words(words, cc.get("words_per_caption", 3))
    lines = []
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        chunk_end = chunk[-1]["end"] + HOLD
        if next_start is not None and next_start - chunk[-1]["end"] < 0.6:
            chunk_end = next_start
        elif next_start is not None:
            chunk_end = min(chunk_end, next_start)
        tokens = [w["word"].upper().replace("{", "").replace("}", "").replace("\\", "") for w in chunk]
        for j, w in enumerate(chunk):
            start = w["start"]
            end = chunk[j + 1]["start"] if j + 1 < len(chunk) else chunk_end
            text = " ".join(f"{{\\c{hi}}}{t}{{\\c{base}}}" if k == j else t for k, t in enumerate(tokens))
            if j == 0:
                text = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" + text
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(max(end, start + 0.05))},Caption,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"
