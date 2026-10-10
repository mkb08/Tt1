"""Alerts: always to a log file and stdout; optionally Telegram and the
Android notification shade (Termux:API)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request


class Notifier:
    def __init__(self, log_path: str = "logs/tt.log", title: str = "tt"):
        self.log_path, self.title = log_path, title
        self.tg_token = os.environ.get("TT_TELEGRAM_TOKEN", "")
        self.tg_chat = os.environ.get("TT_TELEGRAM_CHAT", "")
        self.termux = shutil.which("termux-notification")
        d = os.path.dirname(log_path)
        if d:
            os.makedirs(d, exist_ok=True)

    def log(self, msg: str) -> None:
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        print(line, flush=True)
        with open(self.log_path, "a") as f:
            f.write(line + "\n")

    def alert(self, msg: str) -> None:
        """Log and push to the phone. Never raises: alerts must not kill the bot."""
        self.log("ALERT " + msg)
        if self.tg_token and self.tg_chat:
            try:
                data = urllib.parse.urlencode({"chat_id": self.tg_chat, "text": f"[{self.title}] {msg}"})
                urllib.request.urlopen(
                    f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                    data=data.encode(), timeout=10).read()
            except Exception as e:  # noqa: BLE001
                self.log(f"telegram failed: {e}")
        if self.termux:
            try:
                subprocess.run([self.termux, "--title", self.title, "--content", msg],
                               timeout=10, check=False)
            except Exception as e:  # noqa: BLE001
                self.log(f"termux-notification failed: {e}")


def dumps(d) -> str:
    return json.dumps(d, default=str, separators=(",", ":"))
