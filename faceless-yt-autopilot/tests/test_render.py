import json
import shutil
import subprocess

import pytest

from autopilot import render, visuals
from autopilot.config import ROOT, load_config
from autopilot.media import probe_duration
from autopilot.schema import load_script

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _dims(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return tuple(map(int, out.split(",")))


def test_offline_render_produces_a_short(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "WORK", tmp_path)
    cfg = load_config()
    script = load_script(ROOT / "examples" / "sample-script.json")
    script.scenes = script.scenes[:3]
    workdir = render.produce(script, ROOT / "examples" / "sample-script.json", cfg, offline=True)

    final = workdir / "final.mp4"
    assert _dims(final) == (1080, 1920)
    meta = json.loads((workdir / "metadata.json").read_text())
    assert meta["offline"] is True
    assert "https://pubmed.ncbi.nlm.nih.gov/42716561/" in meta["description"]
    assert "#shorts" in meta["description"]
    assert abs(probe_duration(final) - meta["duration"]) < 0.1
    assert (workdir / "thumbnail.jpg").exists()


def test_stock_clip_segment_with_overlay(tmp_path):
    cfg = load_config()
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25",
                    "-t", "2", "-pix_fmt", "yuv420p", str(clip)], check=True)
    overlay = visuals.text_overlay("Two hours a week", tmp_path / "o.png", 1080, 1920, cfg)
    out = tmp_path / "seg.mp4"
    render._segment_from_clip(clip, overlay, 3.5, out, 1080, 1920, 30)  # longer than the clip: loops
    assert _dims(out) == (1080, 1920)
    assert abs(probe_duration(out) - 3.5) < 0.1


def test_stock_library_widens_search_and_never_reuses(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "x")
    cfg = load_config()
    lib = visuals.StockLibrary(cfg, tmp_path, 1080, 1920)
    pool = {
        "woman jogging sunrise": [],
        "woman jogging": [{"key": "pexels-1", "duration": 10}],
        "woman": [{"key": "pexels-1", "duration": 10}, {"key": "pexels-2", "duration": 1},
                  {"key": "pexels-3", "duration": 8}],
    }
    monkeypatch.setattr(lib, "_search", lambda q: [dict(c, url="", author="a", page="p", provider="Pexels")
                                                   for c in pool.get(q, [])])
    monkeypatch.setattr(lib, "_download", lambda c: tmp_path / f"{c['key']}.mp4")
    clips = lib.clips("woman jogging sunrise", 3, "healthy lifestyle")
    assert [c.name for c in clips] == ["pexels-1.mp4", "pexels-3.mp4"]   # pexels-2 too short
    assert lib.clips("woman jogging", 1, "x") == []                       # already used
