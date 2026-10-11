"""Command line: python -m autopilot <command>

  research [--topic T]        gather evidence and trend signals -> work/research.json
  write [--topic T]           Claude (Anthropic API) drafts + fact-checks a script -> content/queue/
  validate [PATH ...]         check queued scripts (schema, limits, citations exist on PubMed)
  next                        report whether a script is waiting in the queue
  produce [PATH] [--offline]  render the next queued script (or PATH) -> work/<slug>/final.mp4
  upload                      upload the last produced video and archive its script
  run [--topic T]             research + write (if the queue is empty), produce, upload
  analytics                   pull channel stats -> data/performance.json (+ insights with an API key)
"""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import research as research_mod
from . import state
from .config import INSIGHTS, RESEARCH, WORK, load_config
from .schema import check_script, load_script, research_ids


def _validate(path: Path, cfg: dict, online: bool) -> list[str]:
    try:
        script = load_script(path)
    except Exception as exc:
        return [f"invalid script JSON: {exc}"]
    errors, warnings = check_script(script, cfg, research_ids(RESEARCH))
    for w in warnings:
        print(f"  warning: {w}")
    if online and not errors:
        errors += research_mod.verify_sources(script)
    return errors


def cmd_research(args, cfg):
    research_mod.run(cfg, args.topic)


def cmd_write(args, cfg):
    from . import writer

    if args.topic or not RESEARCH.exists():
        research_mod.run(cfg, args.topic)
    data = json.loads(RESEARCH.read_text(encoding="utf-8"))
    if not data["evidence"] and cfg["writer"].get("require_sources", True):
        sys.exit("No evidence found. Broaden research.pubmed.queries or days in config/channel.yaml.")
    writer.write(cfg, data, research_ids(RESEARCH))


def cmd_validate(args, cfg):
    paths = [Path(p) for p in args.paths] or state.queued()
    if not paths:
        sys.exit("Nothing to validate: the queue is empty.")
    failed = False
    for path in paths:
        errors = _validate(path, cfg, online=not args.offline)
        print(f"{'FAIL' if errors else 'OK  '} {path}")
        for e in errors:
            print(f"  - {e}")
        failed |= bool(errors)
    sys.exit(1 if failed else 0)


def cmd_next(args, cfg):
    queue = state.queued()
    state.github_output(pending=str(bool(queue)).lower(), script=queue[0] if queue else "")
    print(queue[0] if queue else "queue is empty")


def cmd_produce(args, cfg):
    from . import render

    path = Path(args.path) if args.path else (state.queued() or [None])[0]
    if path is None:
        sys.exit("The queue is empty. Run `write`, or ask @claude in an issue for a video.")
    errors = _validate(path, cfg, online=not args.offline)
    if errors:
        if not args.path or path.parent.resolve() == state.QUEUE.resolve():
            state.archive(path, ok=False, notes="\n".join(errors))
        sys.exit(f"{path} failed validation and was moved to content/rejected:\n- " + "\n- ".join(errors))
    workdir = render.produce(load_script(path), path, cfg, offline=args.offline)
    meta = json.loads((workdir / "metadata.json").read_text(encoding="utf-8"))
    state.github_output(title=meta["title"], workdir=workdir)


def cmd_upload(args, cfg):
    from . import youtube

    latest = WORK / "latest.json"
    if not latest.exists():
        sys.exit("Nothing produced yet. Run `produce` first.")
    workdir = Path(json.loads(latest.read_text())["workdir"])
    meta = json.loads((workdir / "metadata.json").read_text(encoding="utf-8"))
    if meta.get("offline"):
        sys.exit("Refusing to upload an offline (placeholder audio) render.")
    script_path = Path(meta["script"])
    entry = {
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "slug": meta["slug"], "topic": meta["topic"], "title": meta["title"],
        "source_ids": meta["source_ids"], "video_id": None, "url": None, "publish_at": None,
    }
    if youtube.has_oauth():
        print(f"Upload: '{meta['title']}'")
        video_id, publish_at = youtube.upload(meta, cfg)
        entry.update(video_id=video_id, url=f"https://youtu.be/{video_id}", publish_at=publish_at)
        print(f"Upload: {entry['url']}" + (f" (goes public {publish_at})" if publish_at else ""))
    else:
        print("Upload: no YouTube secrets; render-only mode. Download the video from the workflow artifacts.")
    state.append_history(entry)
    if script_path.exists() and script_path.parent.resolve() == state.QUEUE.resolve():
        state.archive(script_path, ok=True)


def cmd_run(args, cfg):
    if not state.queued():
        cmd_write(args, cfg)
    args.path = None
    cmd_produce(args, cfg)
    if not args.offline:
        cmd_upload(args, cfg)


def cmd_analytics(args, cfg):
    from . import analytics

    performance = analytics.collect()
    if os.environ.get("ANTHROPIC_API_KEY"):
        from . import writer

        INSIGHTS.write_text(writer.insights(cfg, performance), encoding="utf-8")
        print(f"Analytics: insights -> {INSIGHTS}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="autopilot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to channel.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("research", "write", "run"):
        p = sub.add_parser(name)
        p.add_argument("--topic")
        if name == "run":
            p.add_argument("--offline", action="store_true")
    p = sub.add_parser("validate")
    p.add_argument("paths", nargs="*")
    p.add_argument("--offline", action="store_true", help="skip the PubMed citation check")
    sub.add_parser("next")
    p = sub.add_parser("produce")
    p.add_argument("path", nargs="?")
    p.add_argument("--offline", action="store_true", help="silent audio + title cards, no network")
    sub.add_parser("upload")
    sub.add_parser("analytics")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    globals()[f"cmd_{args.command}"](args, cfg)


if __name__ == "__main__":
    main()
