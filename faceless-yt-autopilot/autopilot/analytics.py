"""Pull channel stats so Claude can learn what works and steer the next topics."""

import datetime as dt
import json
import re

from . import state, youtube
from .config import PERFORMANCE


def _seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def collect() -> dict:
    raw = youtube.channel_stats()
    topics = {h.get("video_id"): h.get("topic") for h in state.load_history()}
    now = dt.datetime.now(dt.timezone.utc)
    videos = []
    for v in raw["videos"]:
        published = dt.datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        age_days = max((now - published).total_seconds() / 86400, 0.5)
        stats = v.get("statistics", {})
        views = int(stats.get("viewCount", 0))
        videos.append({
            "id": v["id"],
            "title": v["snippet"]["title"],
            "topic": topics.get(v["id"]),
            "published": v["snippet"]["publishedAt"],
            "privacy": v.get("status", {}).get("privacyStatus"),
            "seconds": _seconds(v.get("contentDetails", {}).get("duration", "")),
            "age_days": round(age_days, 1),
            "views": views,
            "views_per_day": round(views / age_days, 1),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
        })
    videos.sort(key=lambda x: x["views_per_day"], reverse=True)
    ch = raw["channel"]["statistics"]
    performance = {
        "collected_at": now.isoformat(timespec="seconds"),
        "subscribers": int(ch.get("subscriberCount", 0)),
        "total_views": int(ch.get("viewCount", 0)),
        "video_count": int(ch.get("videoCount", 0)),
        "videos": videos,
    }
    PERFORMANCE.parent.mkdir(parents=True, exist_ok=True)
    PERFORMANCE.write_text(json.dumps(performance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Analytics: {len(videos)} videos, {performance['subscribers']} subscribers -> {PERFORMANCE}")
    return performance
