#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — torcore.py — Tor Transport Layer
============================================
Pure mechanical Tor operations. No intelligence, no analysis, no search engine
logic — just routing traffic through the Tor network.

The orchestrator (Claude) calls these functions via bash to interact with the
Tor network. All strategic decisions live in the orchestrator.

Functions:
    tor_session()      → requests.Session routed through Tor SOCKS5
    check_tor()        → verify Tor is running, return exit IP
    renew_identity()   → rotate Tor circuit (NEWNYM)
    fetch(url)         → GET a URL through Tor, return structured result
    TorPool            → optional multi-circuit pool for higher throughput

Dependencies: pip install requests[socks] stem python-dotenv
Requires:    Tor running locally (socks://127.0.0.1:9050)
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import socket
import time
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── config from environment ────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=False)
except ImportError:
    pass

__version__ = "1.0.0"

TOR_SOCKS_HOST   = os.getenv("TOR_SOCKS_HOST", "127.0.0.1")
TOR_SOCKS_PORT   = int(os.getenv("TOR_SOCKS_PORT", "9050"))
TOR_CONTROL_HOST = os.getenv("TOR_CONTROL_HOST", "127.0.0.1")
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
TOR_CONTROL_PASS = os.getenv("TOR_CONTROL_PASSWORD")
TOR_DATA_DIR     = os.getenv("TOR_DATA_DIR")
TOR_TIMEOUT      = int(os.getenv("TOR_TIMEOUT", "45"))
MAX_CONTENT_CHARS = int(os.getenv("OPENTOR_MAX_CHARS", "8000"))
TOR_POOL_SIZE    = int(os.getenv("OPENTOR_POOL_SIZE", "0"))
POOL_BASE_PORT   = int(os.getenv("OPENTOR_POOL_BASE_PORT", "9060"))

log = logging.getLogger("torcore")

# ── rotating user agents ──────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (X11; Linux i686; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_5) AppleWebKit/605.1.15 Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36 Edg/135.0.3179.54",
]

# ── content safety blacklist ──────────────────────────────────────
_CONTENT_BLACKLIST: frozenset[str] = frozenset({
    "csam", "child porn", "childporn", "pedo", "paedo",
    "lolita", "loli ", "lolicon", "shotacon",
    "preteen sex", "preteen nude",
    "underage sex", "underage nude", "underage porn",
    "jailbait", "jail bait",
    "teen porn", "teenporn", "teens sex", "teen nude",
    "child erotica", "child sex", "child nude",
    "boy lover", "girl lover", "boylover", "girllover",
    "toddler sex", "infant sex", "baby sex",
    "incest child", "minor sex", "minors sex",
    "hurtcore",
    "snuff film", "snuff video", "red room",
    "rape video", "rape film", "rape porn", "torture porn",
    "torture murder", "torture video",
    "kids sex", "kids porn", "kids nude",
    "child rape", "child torture", "child murder",
    "minor rape",
})

_TOKEN_PAIRS: tuple[tuple[str, str], ...] = (
    ("child", "rape"), ("child", "torture"), ("child", "minor"),
    ("minor", "rape"), ("minor", "torture"),
    ("kids", "rape"), ("kids", "sex"), ("kids", "porn"), ("kids", "child"),
    ("baby", "rape"), ("infant", "rape"), ("teen", "rape"), ("snuff", "live"),
)


def is_content_safe(text: str) -> bool:
    """Return False if text contains any blacklisted phrase or token pair (case-insensitive)."""
    lower = text.lower()
    if any(term in lower for term in _CONTENT_BLACKLIST):
        return False
    if re.search(r'\brape\b', lower):
        if any(kw in lower for kw in ("video", "film", "porn", "site", "photo",
                                       "image", "stream", "dark web", "onion",
                                       "market", "child", "minor", "teen",
                                       "kids", "baby", "infant")):
            return False
    tokens = set(re.findall(r"[a-z]+", lower))
    if any(a in tokens and b in tokens for a, b in _TOKEN_PAIRS):
        return False
    return True


# ── friendly error messages ────────────────────────────────────────
def _friendly_error(exc: Exception) -> str:
    """Convert raw exceptions into human-readable messages."""
    msg = str(exc)
    patterns = [
        (r"SOCKS5|SOCKS proxy", "Tor circuit unavailable — is `tor` running?"),
        (r"Max retries exceeded", "Tor circuit slow/overloaded — try renewing identity"),
        (r"timed out|Read timed out", "Tor circuit timed out — hidden service may be offline"),
        (r"Connection refused", "Connection refused — hidden service is likely down"),
        (r"SSL|certificate|cert verify", "TLS error — try HTTP instead of HTTPS for this .onion"),
        (r"RemoteDisconnected|ConnectionReset", "Connection reset — site may be overloaded"),
    ]
    for pattern, friendly in patterns:
        if re.search(pattern, msg, re.IGNORECASE):
            return friendly
    return msg[:200]


# ── Tor session ────────────────────────────────────────────────────
def tor_session(port: int = TOR_SOCKS_PORT) -> requests.Session:
    """Build a requests.Session that routes all traffic through Tor SOCKS5.

    Args:
        port: SOCKS5 port (default 9050). Override for TorPool circuits.

    Returns:
        requests.Session with SOCKS5 proxy configured.
    """
    sess = requests.Session()
    retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    proxy = f"socks5h://{TOR_SOCKS_HOST}:{port}"
    sess.proxies = {"http": proxy, "https": proxy}
    return sess


def _tor_port_open() -> bool:
    """Check if the Tor SOCKS port accepts TCP connections."""
    try:
        with socket.create_connection((TOR_SOCKS_HOST, TOR_SOCKS_PORT), timeout=2.0):
            return True
    except OSError:
        return False


# ── check Tor ──────────────────────────────────────────────────────
def check_tor() -> dict:
    """Verify Tor is running and confirm the exit node.

    Returns:
        {"tor_active": bool, "exit_ip": str|None, "error": str|None}
    """
    if not _tor_port_open():
        return {
            "tor_active": False,
            "exit_ip": None,
            "error": f"Tor SOCKS port {TOR_SOCKS_HOST}:{TOR_SOCKS_PORT} not reachable",
        }
    try:
        s = tor_session()
        r = s.get("https://check.torproject.org/api/ip", timeout=TOR_TIMEOUT)
        d = r.json()
        return {"tor_active": d.get("IsTor", False), "exit_ip": d.get("IP"), "error": None}
    except Exception as e:
        return {"tor_active": False, "exit_ip": None, "error": str(e)}


# ── renew identity ─────────────────────────────────────────────────
def renew_identity() -> dict:
    """Rotate Tor circuit — get a new exit node.

    Auth order: TOR_CONTROL_PASSWORD → cookie from TOR_DATA_DIR →
    cookie from common paths → null auth.

    Returns:
        {"success": bool, "error": str|None}
    """
    _COOKIE_PATHS = [
        os.path.join(TOR_DATA_DIR, "control_auth_cookie") if TOR_DATA_DIR else None,
        "/tmp/tor_data/control_auth_cookie",
        "/var/lib/tor/control_auth_cookie",
        "/run/tor/control.authcookie",
        os.path.expanduser("~/.tor/control_auth_cookie"),
    ]

    def _find_cookie() -> Optional[bytes]:
        for path in _COOKIE_PATHS:
            if path and os.path.isfile(path):
                try:
                    with open(path, "rb") as fh:
                        return fh.read()
                except Exception:
                    continue
        return None

    try:
        from stem import Signal
        from stem.control import Controller

        with Controller.from_port(address=TOR_CONTROL_HOST, port=TOR_CONTROL_PORT) as ctrl:
            authed = False
            last_err = "all auth methods failed"

            if TOR_CONTROL_PASS and not authed:
                try:
                    ctrl.authenticate(password=TOR_CONTROL_PASS)
                    authed = True
                except Exception as e:
                    last_err = str(e)

            if not authed:
                cookie = _find_cookie()
                if cookie is not None:
                    try:
                        ctrl.authenticate(password=cookie)
                        authed = True
                    except Exception as e:
                        last_err = str(e)

            if not authed:
                for pw in ("", None):
                    try:
                        ctrl.authenticate(password=pw) if pw is not None else ctrl.authenticate()
                        authed = True
                        break
                    except Exception as e:
                        last_err = str(e)

            if not authed:
                return {"success": False, "error": f"Control port auth failed ({last_err})"}

            ctrl.signal(Signal.NEWNYM)
        time.sleep(1.5)
        return {"success": True, "error": None}
    except ImportError:
        return {"success": False, "error": "stem not installed — pip install stem"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── fetch ──────────────────────────────────────────────────────────
def fetch(url: str, max_chars: int = MAX_CONTENT_CHARS) -> dict:
    """Fetch any URL through Tor — clearnet or .onion.

    Returns:
        {
            "url": str, "is_onion": bool, "status": int,
            "title": str|None, "text": str, "links": list[{"text","href"}],
            "truncated": bool, "error": str|None
        }
    """
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    parsed = urlparse(url)
    is_onion = ".onion" in (parsed.hostname or "")

    # Try HTTPS first, then HTTP for .onion
    urls_to_try = [url]
    if url.startswith("https://") and is_onion:
        urls_to_try.append("http://" + url[8:])

    last_err = "unknown error"
    headers = {"User-Agent": random.choice(_USER_AGENTS)}

    for attempt_url in urls_to_try:
        for _socks_attempt in range(2):
            try:
                sess = tor_session()
                resp = sess.get(attempt_url, headers=headers, timeout=TOR_TIMEOUT)

                # Block .onion → clearnet redirects (de-anonymization risk)
                final_url = resp.url if isinstance(resp.url, str) else attempt_url
                if is_onion and ".onion" not in (urlparse(final_url).hostname or ""):
                    return {
                        "url": attempt_url, "is_onion": True, "status": resp.status_code,
                        "title": None, "text": "", "links": [], "truncated": False,
                        "error": "Security: .onion redirected to clearnet — blocked",
                    }

                # Safety check
                if not is_content_safe(attempt_url):
                    return {
                        "url": attempt_url, "is_onion": is_onion, "status": resp.status_code,
                        "title": "[blocked]", "text": "", "links": [], "truncated": False,
                        "error": "Content matches safety blacklist",
                    }

                # Parse HTML
                try:
                    from bs4 import BeautifulSoup
                except ImportError:
                    return {"url": attempt_url, "is_onion": is_onion, "status": resp.status_code,
                            "title": None, "text": resp.text[:max_chars], "links": [],
                            "truncated": len(resp.text) > max_chars, "error": None}

                if resp.encoding and resp.encoding.upper() in ("ISO-8859-1", "LATIN-1"):
                    resp.encoding = resp.apparent_encoding or "utf-8"

                soup = BeautifulSoup(resp.text, "html.parser")
                title = soup.title.string.strip() if soup.title and soup.title.string else None

                if not is_content_safe((title or "") + " " + final_url):
                    return {"url": final_url, "is_onion": is_onion, "status": resp.status_code,
                            "title": "[blocked]", "text": "", "links": [], "truncated": False,
                            "error": "Content matches safety blacklist"}

                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                raw_text = re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n")).strip()
                truncated = len(raw_text) > max_chars
                body = raw_text[:max_chars]

                base = urlparse(final_url)
                links = []
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.startswith("/"):
                        href = f"{base.scheme}://{base.netloc}{href}"
                    if href.startswith(("http://", "https://")):
                        links.append({"text": a.get_text(strip=True), "href": href})

                return {
                    "url": final_url, "is_onion": is_onion, "status": resp.status_code,
                    "title": title, "text": body, "links": links[:80],
                    "truncated": truncated, "error": None,
                }

            except Exception as exc:
                last_err = _friendly_error(exc)
                if any(kw in str(exc) for kw in ("SOCKS", "timed out", "Connection refused")):
                    time.sleep(1.5)
                    continue
                break

    return {"url": url, "is_onion": is_onion, "status": 0,
            "title": None, "text": "", "links": [], "truncated": False, "error": last_err}


# ── TorPool (optional — multiple circuits) ────────────────────────
class TorPool:
    """Spawn multiple independent Tor processes for parallel scraping.

    When OPENTOR_POOL_SIZE > 0, this pool manages N independent Tor circuits
    on consecutive SOCKS ports (9060, 9061, …). Each circuit gets a unique
    exit identity. Round-robin distribution across circuits.

    Usage:
        pool = TorPool(size=3)
        pool.start()
        session = pool.session()  # get next circuit's session
        pool.stop()
    """

    def __init__(self, size: int = TOR_POOL_SIZE, base_port: int = POOL_BASE_PORT):
        import subprocess
        import tempfile
        import threading
        self.size = size
        self.base_port = base_port
        self._procs = []
        self._dirs = []
        self._lock = threading.Lock()
        self._idx = 0
        self._running = False

    def start(self) -> None:
        import subprocess
        import tempfile
        if self._running or self.size <= 0:
            return
        self._dirs = [tempfile.mkdtemp(prefix=f"opentor_tor_{i}_") for i in range(self.size)]
        for i, ddir in enumerate(self._dirs):
            sp = self.base_port + i
            cp = self.base_port + 100 + i
            torrc = os.path.join(ddir, "torrc")
            with open(torrc, "w") as f:
                f.write(f"SocksPort {sp}\nControlPort {cp}\nDataDirectory {ddir}\n"
                        f"CookieAuthentication 1\nSafeSocks 1\n")
            proc = subprocess.Popen(["tor", "-f", torrc],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._procs.append(proc)
        deadline = time.time() + 30
        for i in range(self.size):
            sp = self.base_port + i
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", sp), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.5)
        self._running = True

    def stop(self) -> None:
        import shutil
        for proc in self._procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._procs.clear()
        for ddir in self._dirs:
            shutil.rmtree(ddir, ignore_errors=True)
        self._dirs.clear()
        self._running = False

    def session(self) -> requests.Session:
        if not self._running or not self.size:
            return tor_session()
        with self._lock:
            i = self._idx % self.size
            self._idx += 1
        return tor_session(port=self.base_port + i)

    def renew_all(self) -> list[dict]:
        from stem import Signal
        from stem.control import Controller
        results = []
        for i in range(self.size):
            cp = self.base_port + 100 + i
            ddir = self._dirs[i] if i < len(self._dirs) else ""
            cookie_path = os.path.join(ddir, "control_auth_cookie")
            try:
                with Controller.from_port(address="127.0.0.1", port=cp) as c:
                    if os.path.isfile(cookie_path):
                        with open(cookie_path, "rb") as fh:
                            c.authenticate(password=fh.read())
                    else:
                        c.authenticate()
                    c.signal(Signal.NEWNYM)
                results.append({"port": cp, "success": True})
            except Exception as e:
                results.append({"port": cp, "success": False, "error": str(e)})
        return results


# Module-level pool singleton
_pool: Optional[TorPool] = None
import threading as _th


def _get_pool() -> Optional[TorPool]:
    global _pool
    if TOR_POOL_SIZE <= 0:
        return None
    if _pool is None or not _pool._running:
        _pool = TorPool()
        _pool.start()
    return _pool


def pool_session() -> requests.Session:
    """Get a session — from the pool if active, else standard Tor."""
    p = _get_pool()
    return p.session() if p else tor_session()
