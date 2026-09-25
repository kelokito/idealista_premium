"""A deliberately slow HTTP session: one request at a time, random pauses, backoff on 429/5xx."""
from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class Blocked(RuntimeError):
    """The site refused us (403 / captcha). We stop instead of trying to get around it."""


class PoliteSession:
    def __init__(self, min_delay: float = 1.5, max_delay: float = 3.5, timeout: float = 30,
                 user_agent: str = BROWSER_UA, retries: int = 3):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "es-ES,es;q=0.9,ca;q=0.8,en;q=0.7",
        })
        self.min_delay, self.max_delay = min_delay, max_delay
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0

    def _wait(self) -> None:
        pause = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.monotonic() - self._last
        if elapsed < pause:
            time.sleep(pause - elapsed)

    def request(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        for attempt in range(1, self.retries + 1):
            self._wait()
            try:
                r = self.s.request(method, url, **kw)
            except requests.RequestException as e:
                log.warning("%s %s failed (%s), attempt %d", method, url, e, attempt)
                self._last = time.monotonic()
                time.sleep(5 * attempt)
                continue
            self._last = time.monotonic()
            if r.status_code == 403:
                raise Blocked(f"403 from {url}")
            if r.status_code == 429 or r.status_code >= 500:
                wait = 15 * attempt
                log.warning("%s -> %d, backing off %ds", url, r.status_code, wait)
                time.sleep(wait)
                continue
            return r
        raise RuntimeError(f"giving up on {url} after {self.retries} attempts")

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)


def from_config(cfg: dict) -> PoliteSession:
    h = cfg.get("http", {})
    return PoliteSession(h.get("min_delay_s", 1.5), h.get("max_delay_s", 3.5), h.get("timeout_s", 30))
