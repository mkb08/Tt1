import json
from types import SimpleNamespace

import pytest

from autopilot import research, writer
from autopilot.config import ROOT, load_config
from autopilot.schema import load_script

SAMPLE = ROOT / "examples" / "sample-script.json"
EVIDENCE = [{"id": "PMID:42716561", "kind": "study", "title": "t", "abstract": "a", "url": "u"}]


class FakeMessages:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, parsed_output=self.outputs.pop(0))


def _install(monkeypatch, outputs):
    messages = FakeMessages(outputs)
    monkeypatch.setattr(writer, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def test_write_retries_after_failed_fact_check(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "QUEUE", tmp_path)
    script = load_script(SAMPLE)
    messages = _install(monkeypatch, [
        script, writer.FactCheck(verdict="fail", issues=["scene 3: overstated"]),
        script, writer.FactCheck(verdict="pass", issues=[]),
    ])
    path = writer.write(load_config(), {"requested_topic": "", "recent_topics": [], "insights": "",
                                        "evidence": EVIDENCE, "signals": {}}, {"PMID:42716561"})
    assert load_script(path).title == script.title
    assert len(messages.calls) == 4
    assert "overstated" in messages.calls[2]["messages"][0]["content"]
    first = messages.calls[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default" and first["betas"] == [writer.FALLBACK_BETA]


def test_write_gives_up_after_two_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "QUEUE", tmp_path)
    script = load_script(SAMPLE)
    bad = writer.FactCheck(verdict="fail", issues=["unsupported claim"])
    _install(monkeypatch, [script, bad, script, bad])
    with pytest.raises(writer.WriterError, match="unsupported claim"):
        writer.write(load_config(), {"requested_topic": "", "recent_topics": [], "insights": "",
                                     "evidence": EVIDENCE, "signals": {}}, None)
    assert list(tmp_path.iterdir()) == []


def test_refusal_is_reported(monkeypatch):
    refused = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="bio"), parsed_output=None)
    monkeypatch.setattr(writer, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(
        messages=SimpleNamespace(parse=lambda **kw: refused))))
    with pytest.raises(writer.WriterError, match="bio"):
        writer.draft(load_config(), {"requested_topic": "", "recent_topics": [], "insights": "",
                                     "evidence": [], "signals": {}})


EFETCH = b"""<?xml version="1.0"?>
<PubmedArticleSet>
 <PubmedArticle><MedlineCitation><PMID>111</PMID><Article>
  <Journal><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue><Title>BMJ</Title></Journal>
  <ArticleTitle>Walking and <i>bone</i> density.</ArticleTitle>
  <Abstract><AbstractText Label="RESULTS">124 trials.</AbstractText><AbstractText Label="CONCLUSIONS">It helps.</AbstractText></Abstract>
  <PublicationTypeList><PublicationType>Meta-Analysis</PublicationType></PublicationTypeList>
 </Article></MedlineCitation></PubmedArticle>
 <PubmedArticle><MedlineCitation><PMID>222</PMID><Article>
  <ArticleTitle>Retracted study</ArticleTitle>
  <PublicationTypeList><PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
 </Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def test_pubmed_parsing_skips_retractions(monkeypatch):
    monkeypatch.setattr(research, "_eutils", lambda *a, **k: SimpleNamespace(content=EFETCH))
    items = research.pubmed_fetch(["111", "222"])
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "PMID:111" and item["year"] == "2026" and item["journal"] == "BMJ"
    assert item["title"] == "Walking and bone density."
    assert item["abstract"] == "RESULTS: 124 trials.\nCONCLUSIONS: It helps."


def test_verify_sources_flags_missing_and_mismatched(monkeypatch):
    script = load_script(SAMPLE)
    script.sources.append(script.sources[0].model_copy(update={"id": "PMID:1"}))
    summary = {"result": {"42716561": {"title": "Something else entirely about cats."}, "1": {"error": "cannot get document summary"}}}
    monkeypatch.setattr(research, "_eutils", lambda *a, **k: SimpleNamespace(json=lambda: summary))
    errors = research.verify_sources(script)
    assert any("title mismatch" in e for e in errors)
    assert any("PMID:1 does not exist" in e for e in errors)
