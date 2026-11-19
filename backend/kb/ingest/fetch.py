"""Plain HTTP downloads for layer 3 sources (docs/ingestion-plan.md §2).

Generic client name, no e-mail. NCBI E-utilities are throttled to `max_requests_per_second` (sources.toml).
"""

import hashlib
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from kb.ingest.config import SOURCE_SETTINGS

USER_AGENT = "hpo-g14"
ATTEMPTS = 3
BUSY_WAIT = 20  # seconds × attempt after 429 / 5xx (server busy or briefly down)
EUTILS = SOURCE_SETTINGS["eutils"]
_last_call: dict[str, float] = {}


def _throttle(key: str, per_second: float) -> None:
    wait = _last_call.get(key, 0) + 1 / per_second - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[key] = time.monotonic()


def _open(url: str, data: bytes | None = None, timeout: float = 300):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def get(url: str, *, params: dict | None = None, data: dict | None = None, throttle: str | None = None,
        per_second: float = 3) -> bytes:
    """GET (or POST when `data`) with up to 3 attempts; 404 is raised at once."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None
    for attempt in range(1, ATTEMPTS + 1):
        if throttle:
            _throttle(throttle, per_second)
        try:
            with _open(url, body) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == ATTEMPTS:
                raise
            time.sleep(BUSY_WAIT * attempt if e.code == 429 or e.code >= 500 else 2 * attempt)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == ATTEMPTS:
                raise
        time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def eutils(url: str, **params) -> bytes:
    """NCBI E-utilities: POST (long id lists), tool name, no e-mail, rate-limited."""
    return get(url, data={**params, "tool": EUTILS["tool"]}, throttle="eutils",
               per_second=EUTILS["max_requests_per_second"])


def download(url: str, dest: Path) -> Path:
    """Stream a (possibly large) file to `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with _open(url) as r, tmp.open("wb") as f:
                shutil.copyfileobj(r, f, length=1 << 20)
            tmp.rename(dest)
            return dest
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == ATTEMPTS:
                raise
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
