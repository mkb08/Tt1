"""Collect topic ideas and citable evidence from free sources.

Output (work/research.json):
  evidence  - citable items (PubMed studies, RSS articles) with ids the script must cite
  signals   - what people are searching and watching right now (not citable)
"""

import datetime as dt
import difflib
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import requests

from . import state
from .config import INSIGHTS, RESEARCH

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TRENDS_NS = {"ht": "https://trends.google.com/trending/rss"}
EXCLUDED_TYPES = {"Retracted Publication", "Retraction of Publication", "Expression of Concern"}
UA = {"User-Agent": "faceless-yt-autopilot/1.0 (+https://github.com)"}


def _eutils(endpoint: str, **params) -> requests.Response:
    params["tool"] = "faceless-yt-autopilot"
    if key := os.environ.get("NCBI_API_KEY"):
        params["api_key"] = key
    for attempt in range(4):
        r = requests.get(f"{EUTILS}/{endpoint}", params=params, headers=UA, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        time.sleep(0.12 if "api_key" in params else 0.4)  # NCBI: 3 req/s without a key, 10 with
        return r
    r.raise_for_status()
    return r


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def pubmed_search(query: str, days: int, limit: int, publication_types: list[str]) -> list[str]:
    types = " OR ".join(f'"{t}"[pt]' for t in publication_types)
    term = f"({query}) AND ({types}) AND english[lang] AND hasabstract"
    r = _eutils("esearch.fcgi", db="pubmed", term=term, retmax=limit, sort="relevance",
                datetype="pdat", reldate=days, retmode="json")
    return r.json()["esearchresult"]["idlist"]


def pubmed_fetch(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    root = ET.fromstring(_eutils("efetch.fcgi", db="pubmed", id=",".join(pmids), retmode="xml").content)
    items = []
    for art in root.iter("PubmedArticle"):
        pmid = art.findtext(".//MedlineCitation/PMID")
        types = [_text(t) for t in art.iter("PublicationType")]
        if EXCLUDED_TYPES.intersection(types):
            continue
        parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label = ab.get("Label")
            parts.append(f"{label}: {_text(ab)}" if label else _text(ab))
        year = (art.findtext(".//Journal/JournalIssue/PubDate/Year")
                or art.findtext(".//ArticleDate/Year")
                or (art.findtext(".//Journal/JournalIssue/PubDate/MedlineDate") or "")[:4])
        items.append({
            "id": f"PMID:{pmid}",
            "kind": "study",
            "title": _text(art.find(".//ArticleTitle")),
            "journal": art.findtext(".//Journal/Title") or "",
            "year": year,
            "publication_types": types,
            "abstract": "\n".join(parts),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return items


def pubmed(cfg: dict, topic: str | None, used: set[str]) -> list[dict]:
    pc = cfg["research"]["pubmed"]
    queries = [topic] if topic else pc["queries"]
    per_query = pc["per_query"] * (3 if topic else 1)
    seen, items = set(used), []
    for query in queries:
        try:
            ids = pubmed_search(query, pc["days"], per_query + 4, pc["publication_types"])
        except requests.RequestException as exc:
            print(f"  pubmed: '{query}' failed: {exc}")
            continue
        fresh = [i for i in ids if f"PMID:{i}" not in seen][:per_query]
        for item in pubmed_fetch(fresh):
            item["query"] = query
            items.append(item)
            seen.add(item["id"])
    return items


def google_trends(cfg: dict) -> list[dict]:
    geo = cfg["research"].get("google_trends", {}).get("geo", "US")
    r = requests.get("https://trends.google.com/trending/rss", params={"geo": geo}, headers=UA, timeout=30)
    r.raise_for_status()
    trends = []
    for item in ET.fromstring(r.content).iter("item"):
        trends.append({
            "query": item.findtext("title"),
            "traffic": item.findtext("ht:approx_traffic", namespaces=TRENDS_NS),
            "headlines": [n.findtext("ht:news_item_title", namespaces=TRENDS_NS)
                          for n in item.findall("ht:news_item", TRENDS_NS)][:2],
        })
    return trends


def youtube_popular(cfg: dict) -> list[dict]:
    from . import youtube

    yc = cfg["research"].get("youtube_popular", {})
    yt = youtube.client(readonly=True)
    if yt is None:
        print("  youtube_popular: skipped (set YOUTUBE_API_KEY or the YT_* OAuth secrets)")
        return []
    resp = yt.videos().list(part="snippet,statistics", chart="mostPopular", maxResults=25,
                            regionCode=yc.get("region", "US"),
                            videoCategoryId=yc.get("category_id", "26")).execute()
    return [{"title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
             "views": int(v["statistics"].get("viewCount", 0))} for v in resp.get("items", [])]


def rss(cfg: dict) -> list[dict]:
    items = []
    atom = "{http://www.w3.org/2005/Atom}"
    for url in cfg["research"].get("rss", {}).get("feeds", []):
        try:
            root = ET.fromstring(requests.get(url, headers=UA, timeout=30).content)
        except (requests.RequestException, ET.ParseError) as exc:
            print(f"  rss: {url} failed: {exc}")
            continue
        for entry in list(root.iter("item"))[:10] + list(root.iter(f"{atom}entry"))[:10]:
            link = entry.findtext("link") or (entry.find(f"{atom}link").get("href") if entry.find(f"{atom}link") is not None else "")
            summary = entry.findtext("description") or entry.findtext(f"{atom}summary") or ""
            items.append({
                "id": f"URL:{link}",
                "kind": "article",
                "title": (entry.findtext("title") or entry.findtext(f"{atom}title") or "").strip(),
                "abstract": re.sub(r"<[^>]+>", " ", summary).strip()[:2000],
                "url": link,
            })
    return items


def run(cfg: dict, topic: str | None = None) -> dict:
    topic = (topic or os.environ.get("TOPIC") or "").strip() or None
    sources = cfg["research"]["sources"]
    history = state.load_history()
    used = {sid for h in history for sid in h.get("source_ids", [])}
    research = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "requested_topic": topic or "",
        "niche": cfg["channel"]["niche"],
        "recent_topics": [h["topic"] for h in history[-40:]],
        "insights": INSIGHTS.read_text(encoding="utf-8") if INSIGHTS.exists() else "",
        "evidence": [],
        "signals": {},
    }
    if "pubmed" in sources:
        print("Research: PubMed")
        research["evidence"] += pubmed(cfg, topic, used)
    if "rss" in sources:
        print("Research: RSS")
        research["evidence"] += [i for i in rss(cfg) if i["id"] not in used]
    for name, fn in (("google_trends", google_trends), ("youtube_popular", youtube_popular)):
        if name in sources:
            print(f"Research: {name}")
            try:
                research["signals"][name] = fn(cfg)
            except Exception as exc:  # signals are optional; never fail the run over them
                print(f"  {name} failed: {exc}")
    RESEARCH.parent.mkdir(parents=True, exist_ok=True)
    RESEARCH.write_text(json.dumps(research, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Research: {len(research['evidence'])} evidence items -> {RESEARCH}")
    return research


def _similar(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def verify_sources(script) -> list[str]:
    """Check every PMID the script cites exists on PubMed and matches its stated title."""
    pmids = [s.id.split(":", 1)[1] for s in script.sources if s.id.startswith("PMID:")]
    if not pmids:
        return []
    result = _eutils("esummary.fcgi", db="pubmed", id=",".join(pmids), retmode="json").json().get("result", {})
    errors = []
    for src in script.sources:
        if not src.id.startswith("PMID:"):
            continue
        rec = result.get(src.id.split(":", 1)[1])
        if not rec or "error" in rec:
            errors.append(f"{src.id} does not exist on PubMed")
        elif _similar(src.title, rec.get("title", "")) < 0.6:
            errors.append(f"{src.id} title mismatch: script says '{src.title}', PubMed says '{rec.get('title')}'")
    return errors
