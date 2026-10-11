from pathlib import Path

import pytest

from autopilot import captions
from autopilot.config import ROOT, load_config
from autopilot.schema import check_script, load_script

SAMPLE = ROOT / "examples" / "sample-script.json"


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def script():
    return load_script(SAMPLE)


def test_sample_script_is_valid(script, cfg):
    errors, _ = check_script(script, cfg, research_ids={"PMID:42716561"})
    assert errors == []


def test_invented_citation_is_caught(script, cfg):
    script.scenes[2].source_ids = ["PMID:1"]
    errors, _ = check_script(script, cfg)
    assert any("PMID:1" in e for e in errors)


def test_source_missing_from_research_is_caught(script, cfg):
    errors, _ = check_script(script, cfg, research_ids={"PMID:999"})
    assert any("not in the research file" in e for e in errors)


def test_youtube_limits(script, cfg):
    script.title = "x" * 101
    script.description = "under <5 years"
    errors, _ = check_script(script, cfg)
    assert any("title" in e for e in errors)
    assert any("'<'" in e for e in errors)


def test_uncited_script_fails_when_sources_required(script, cfg):
    for scene in script.scenes:
        scene.source_ids = []
    errors, _ = check_script(script, cfg)
    assert any("no scene cites" in e for e in errors)


def _words(*spec):
    return [{"word": w, "start": s, "end": e, "scene": sc} for w, s, e, sc in spec]


def test_chunks_break_on_pause_scene_and_size():
    words = _words(("a", 0.0, 0.2, 0), ("b", 0.2, 0.4, 0), ("c", 0.4, 0.6, 0), ("d", 0.6, 0.8, 0),
                   ("e", 1.5, 1.7, 0),            # long pause before e
                   ("f", 1.7, 1.9, 1))            # new scene
    chunks = captions.chunk_words(words, 3)
    assert [[w["word"] for w in c] for c in chunks] == [["a", "b", "c"], ["d"], ["e"], ["f"]]


def test_ass_has_one_event_per_word_with_highlight(cfg):
    words = _words(("hello", 0.0, 0.3, 0), ("there", 0.3, 0.6, 0), ("friend", 0.6, 0.9, 0))
    ass = captions.build_ass(words, cfg, 1080, 1920, "Anton")
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 3
    assert "PlayResY: 1920" in ass
    assert events[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.30")
    assert "{\\c&H00E6FF&}THERE" in events[1]


def test_timestamp_format():
    assert captions._ts(3725.456) == "1:02:05.46"
