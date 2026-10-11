"""YouTube Data API v3: upload, thumbnails, and channel stats.

Credentials come from environment variables (GitHub secrets):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN  - OAuth, created once with scripts/get_youtube_token.py
  YOUTUBE_API_KEY                                   - optional, read-only public data
"""

import datetime as dt
import os
import random
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
RETRY_STATUS = {500, 502, 503, 504}


def has_oauth() -> bool:
    return all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def client(readonly: bool = False):
    if has_oauth():
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            None,
            refresh_token=os.environ["YT_REFRESH_TOKEN"],
            client_id=os.environ["YT_CLIENT_ID"],
            client_secret=os.environ["YT_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        return build("youtube", "v3", credentials=creds, cache_discovery=False)
    if readonly and os.environ.get("YOUTUBE_API_KEY"):
        return build("youtube", "v3", developerKey=os.environ["YOUTUBE_API_KEY"], cache_discovery=False)
    return None


def upload(meta: dict, cfg: dict) -> tuple[str, str | None]:
    yt = client()
    if yt is None:
        raise RuntimeError("YouTube OAuth secrets are not set (YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)")
    yc = cfg["youtube"]
    status = {
        "selfDeclaredMadeForKids": yc.get("made_for_kids", False),
        "containsSyntheticMedia": yc.get("contains_synthetic_media", False),
        "embeddable": True,
    }
    delay = float(yc.get("publish_delay_hours", 0))
    if delay > 0:
        publish_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=delay)
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        status["privacyStatus"] = yc.get("privacy_status", "public")
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": str(yc.get("category_id", "26")),
            "defaultLanguage": cfg["channel"].get("language", "en"),
            "defaultAudioLanguage": cfg["channel"].get("language", "en"),
        },
        "status": status,
    }
    media = MediaFileUpload(meta["video"], mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)

    response, attempt = None, 0
    while response is None:
        try:
            progress, response = request.next_chunk()
            if progress:
                print(f"  uploaded {int(progress.progress() * 100)}%")
        except HttpError as exc:
            if exc.resp.status not in RETRY_STATUS or attempt >= 6:
                raise
            attempt += 1
            time.sleep(2 ** attempt + random.random())
        except (ConnectionError, TimeoutError, OSError):
            if attempt >= 6:
                raise
            attempt += 1
            time.sleep(2 ** attempt + random.random())
    video_id = response["id"]

    if yc.get("upload_thumbnail", True) and meta.get("thumbnail"):
        try:
            yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(meta["thumbnail"], mimetype="image/jpeg")).execute()
        except HttpError as exc:
            print(f"  thumbnail not set (custom thumbnails need a phone-verified channel): {exc.reason}")
    return video_id, status.get("publishAt")


def channel_stats() -> dict:
    yt = client()
    if yt is None:
        raise RuntimeError("YouTube OAuth secrets are not set")
    channel = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()["items"][0]
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while len(ids) < 200:
        page = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    videos = []
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="snippet,statistics,contentDetails,status", id=",".join(ids[i:i + 50])).execute()
        videos += resp.get("items", [])
    return {"channel": channel, "videos": videos}
