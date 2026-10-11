"""JSON over HTTPS with the standard library, with retries on rate limits."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "tt/0.1", "Accept": "application/json"}


def get_json(url: str, params: dict | None = None, headers: dict | None = None,
             retries: int = 4, timeout: int = 30):
    """GET `url`; retry 429 and 5xx with exponential backoff."""
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={**UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            body = e.read()[:300].decode(errors="replace")
            raise RuntimeError(f"GET {url} -> HTTP {e.code}: {body}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise
    raise AssertionError("unreachable")


def send_json(method: str, url: str, body: dict | None, headers: dict, timeout: int = 30):
    """POST/DELETE with a JSON body. No retries: orders must not be sent twice."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**UA, "Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body_txt = e.read()[:500].decode(errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {body_txt}") from None
    return json.loads(raw) if raw else {}
