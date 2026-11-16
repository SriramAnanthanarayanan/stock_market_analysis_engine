"""Download of the NSE end-of-day archive files (nsearchives.nseindia.com).

All files are public, free and published by NSE after the close (the F&O
bhavcopy usually in the evening). TLS certificates are always verified (see
app/net.py); the original project's Samco fallback (an undocumented internal
server path fetched with certificate checks turned off) is not used.

Raw files are kept in ``<cache>/bhavcopy/`` so a re-parse never needs the
network; the service deletes them after ``raw_retention_days`` because every
value the feature needs is stored in the database.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import requests

log = logging.getLogger(__name__)

FO_UDIFF_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
FO_LEGACY_URL = "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{yyyy}/{mon}/fo{dd}{mon}{yyyy}bhav.csv.zip"
EQ_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
INDEX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class FetchError(RuntimeError):
    """Transient failure (network, 5xx, HTML instead of a file): retry later."""


def fo_urls(d: date) -> list[tuple[str, str]]:
    mon = d.strftime("%b").upper()
    return [
        ("udiff", FO_UDIFF_URL.format(ymd=d.strftime("%Y%m%d"))),
        ("legacy", FO_LEGACY_URL.format(yyyy=d.strftime("%Y"), mon=mon, dd=d.strftime("%d"))),
    ]


def eq_url(d: date) -> str:
    return EQ_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))


def index_url(d: date) -> str:
    return INDEX_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))


class NseArchiveFetcher:
    """``get(url)`` -> bytes, or None when NSE has no such file (404 / 403)."""

    def __init__(self, timeout_s: float = 45.0, attempts: int = 3, backoff_s: float = 2.0, delay_s: float = 0.3):
        self.timeout_s = timeout_s
        self.attempts = attempts
        self.backoff_s = backoff_s
        self.delay_s = delay_s
        self._session: requests.Session | None = None

    def _sess(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        return self._session

    def get(self, url: str, kind: str) -> bytes | None:
        last: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                time.sleep(self.delay_s)
                r = self._sess().get(url, timeout=self.timeout_s)
                if r.status_code in (403, 404):
                    return None
                if r.status_code != 200:
                    raise FetchError(f"HTTP {r.status_code}")
                body = r.content
                head = body[:300].decode("utf-8", errors="ignore").lower()
                if kind == "zip" and not body.startswith(b"PK"):
                    if "<html" in head or "<!doctype" in head:
                        raise FetchError("HTML page instead of the zip file")
                    raise FetchError("not a zip archive")
                if kind == "csv" and ("<html" in head or "<!doctype" in head):
                    raise FetchError("HTML page instead of the CSV file")
                if len(body) < 200:
                    raise FetchError(f"response too small ({len(body)} bytes)")
                return body
            except (requests.RequestException, FetchError) as exc:
                last = exc
                log.info("NSE archive %s attempt %s/%s failed: %s", url, attempt, self.attempts, exc)
                if attempt < self.attempts:
                    time.sleep(self.backoff_s * attempt)
        raise FetchError(f"{url}: {last}")


def raw_path(root: Path, kind: str, d: date, source: str = "") -> Path:
    ymd = d.strftime("%Y%m%d")
    if kind == "fo":
        return root / "fo" / f"{ymd}_{source}.csv.zip"
    return root / kind / f"{ymd}.csv"
