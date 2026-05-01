#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — engines.py — Dark Web Search Engine Layer
=====================================================
Manages 12 verified-live .onion search engines. Handles querying,
health checks, mode-based engine routing, and result deduplication.

This module knows HOW to search — it does NOT decide WHAT to search or
how to interpret results. That's the orchestrator's job.

Functions:
    search(query, engines, max_results)   → search dark web engines
    check_engines(max_workers, cached)    → health ping all engines
    mode_engines(mode)                    → recommend engines per analysis mode

Engine catalogue adapted from Robin (github.com/apurvsinghgautam/robin, MIT).
. onion addresses verified via dark.fail.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

import torcore

__version__ = torcore.__version__

# ── SQLite-backed search cache (survives process restarts) ────────
import db as _db_mod
_SEARCH_CACHE_TTL = int(os.getenv("OPENTOR_SEARCH_CACHE_TTL", "1800"))  # 30 min


def clear_cache() -> int:
    """Clear all cached search results. Returns number of entries evicted."""
    return _db_mod.get_db().cache_clear("search")


# ── 12 verified-live dark web search engines ──────────────────────
SEARCH_ENGINES = [
    {"name": "Ahmia",
     "url": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q={query}"},
    {"name": "OnionLand",
     "url": "http://3bbad7fauom4d6sgppalyqddsqbf5u5p56b5k5uk2zxsy3d6ey2jobad.onion/search?q={query}"},
    {"name": "Amnesia",
     "url": "http://amnesia7u5odx5xbwtpnqk3edybgud5bmiagu75bnqx2crntw5kry7ad.onion/search?query={query}"},
    {"name": "Torland",
     "url": "http://torlbmqwtudkorme6prgfpmsnile7ug2zm4u3ejpcncxuhpu4k2j4kyd.onion/index.php?a=search&q={query}"},
    {"name": "Excavator",
     "url": "http://2fd6cemt4gmccflhm6imvdfvli3nf7zn6rfrwpsy7uhxrgbypvwf5fad.onion/search?query={query}"},
    {"name": "Onionway",
     "url": "http://oniwayzz74cv2puhsgx4dpjwieww4wdphsydqvf5q7eyz4myjvyw26ad.onion/search.php?s={query}"},
    {"name": "Tor66",
     "url": "http://tor66sewebgixwhcqfnp5inzp5x5uohhdy3kvtnyfxc2e5mxiuh34iid.onion/search?q={query}"},
    {"name": "OSS",
     "url": "http://3fzh7yuupdfyjhwt3ugzqqof6ulbcl27ecev33knxe3u7goi3vfn2qqd.onion/oss/index.php?search={query}"},
    {"name": "Torgol",
     "url": "http://torgolnpeouim56dykfob6jh5r2ps2j73enc42s2um4ufob3ny4fcdyd.onion/?q={query}"},
    {"name": "TheDeepSearches",
     "url": "http://searchgf7gdtauh7bhnbyed4ivxqmuoat3nm6zfrg3ymkq6mtnpye3ad.onion/search?q={query}"},
    {"name": "DuckDuckGo-Tor",
     "url": "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/?q={query}&ia=web"},
    {"name": "Ahmia-clearnet",
     "url": "https://ahmia.fi/search/?q={query}"},
]

# ── keywords for search quality ────────────────────────────────────
_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "or", "in", "on", "at", "to", "a", "an", "of", "for", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "will", "would", "could", "should", "may", "might", "can",
    "this", "that", "these", "those", "it", "its", "from", "with", "as", "by",
    "about", "into", "through", "during", "before", "after", "above", "below",
    "between", "each", "all", "both", "few", "more", "most", "no", "not",
    "only", "same", "so", "than", "too", "very", "just", "but", "if", "then",
    "because", "our", "your", "their", "my", "his", "her", "we", "you", "they",
    "i", "who", "which", "www", "com", "http", "https", "onion", "html",
    "page", "site", "click", "here", "link", "home",
    "forum", "thread", "reply", "post", "message", "user", "admin", "login",
    "register",
})


# ── search ─────────────────────────────────────────────────────────
def search(
    query: str,
    engines: Optional[list[str]] = None,
    max_results: int = 20,
    max_workers: int = 8,
    mode: Optional[str] = None,
    use_cache: bool = True,
) -> list[dict]:
    """Search dark web engines simultaneously through Tor.

    Args:
        query:           Search keywords (≤5 words recommended).
        engines:         Specific engine names, or None for all 12.
        max_results:     Max unique results (deduplicated by URL).
        max_workers:     Parallel search threads.
        mode:            Auto-select engines per analysis mode.
        use_cache:       Use in-memory cache (30 min TTL). Set False to force live.

    Returns:
        [{"title": str, "url": str, "engine": str}, ...]
    """
    # Mode-based engine routing
    if mode and not engines:
        engines = mode_engines(mode)

    selected = SEARCH_ENGINES
    if engines:
        names = {e.lower() for e in engines}
        selected = [e for e in SEARCH_ENGINES if e["name"].lower() in names]

    # Check cache
    if use_cache and not engines and max_workers == 8:
        cache_key = f"{query.lower().strip()}|{','.join(sorted(e['name'] for e in selected))}"
        cached = _db_mod.get_db().cache_get(cache_key, "search", _SEARCH_CACHE_TTL)
        if cached is not None:
            return cached[:max_results]

    results: list[dict] = []
    seen_urls: set[str] = set()

    def _query_one(engine: dict) -> list[dict]:
        url = engine["url"].format(query=quote_plus(query))
        eng_host = re.findall(r"https?://([^/]+)", engine["url"])
        eng_host = eng_host[0] if eng_host else ""
        found = []

        for _attempt in range(3):
            headers = {"User-Agent": random.choice(torcore._USER_AGENTS)}
            try:
                sess = torcore.pool_session() if torcore.TOR_POOL_SIZE > 0 else torcore.tor_session()
                resp = sess.get(url, headers=headers, timeout=torcore.TOR_TIMEOUT)
                if resp.status_code != 200:
                    return []
                soup = BeautifulSoup(resp.text, "html.parser")
                # Try result containers first, fall back to all links
                result_links = (
                    soup.select(".result a, .results a, li.result a, div.result a,"
                                " .search-result a, .web-result a, td.result a")
                    or soup.find_all("a")
                )
                for a in result_links:
                    href = a.get("href", "")
                    title = a.get_text(strip=True)
                    if len(title) < 4:
                        continue
                    # Decode redirect wrappers (Ahmia /redirect/?redirect_url=...)
                    if "redirect_url=" in href:
                        qs = parse_qs(urlparse(href).query)
                        for param in ("redirect_url", "url"):
                            if param in qs:
                                href = unquote(qs[param][0])
                                break
                    # Extract .onion URLs
                    onion = re.findall(r"https?://[a-z0-9.\-]+\.onion[^\s\"'<>]*", href)
                    onion = [u for u in onion if eng_host not in u]
                    # Fallback: clearnet
                    clearnet = []
                    if not onion:
                        clearnet = re.findall(r"https?://[a-z0-9.\-]+\.[a-z]{2,}[^\s\"'<>]*", href)
                        clearnet = [u for u in clearnet if eng_host not in u and ".onion" not in u]
                    picked = onion or clearnet
                    if not picked:
                        continue
                    found.append({
                        "title": title,
                        "url": picked[0].rstrip("/"),
                        "engine": engine["name"],
                    })
                return found
            except Exception:
                continue
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_query_one, eng): eng for eng in selected}
        for future in as_completed(futures):
            for item in future.result():
                clean = item["url"].rstrip("/")
                if clean not in seen_urls:
                    safe_str = item.get("title", "") + " " + item.get("url", "")
                    if torcore.is_content_safe(safe_str):
                        seen_urls.add(clean)
                        results.append(item)

    # Store in cache for future use
    if use_cache and results:
        cache_key = f"{query.lower().strip()}|{','.join(sorted(e['name'] for e in selected))}"
        _db_mod.get_db().cache_set(cache_key, "search", results)

    return results[:max_results]


# ── engine health ──────────────────────────────────────────────────
def check_engines(max_workers: int = 8) -> list[dict]:
    """Ping all 12 engines through Tor. Returns status + latency per engine.

    Returns:
        [{"name": str, "status": "up"|"down", "latency_ms": int|None,
          "error": str|None}, ...] — ordered by original engine list.
    """

    def _ping(engine: dict) -> dict:
        url = engine["url"].format(query="test")
        try:
            sess = torcore.pool_session() if torcore.TOR_POOL_SIZE > 0 else torcore.tor_session()
            sess.headers["User-Agent"] = random.choice(torcore._USER_AGENTS)
            start = time.time()
            resp = sess.get(url, timeout=20)
            latency_ms = round((time.time() - start) * 1000)
            status = "up" if resp.status_code == 200 else "down"
            err = None if resp.status_code == 200 else f"HTTP {resp.status_code}"
            _db_mod.get_db().engine_history_add(engine["name"], status, latency_ms, err)
            return {"name": engine["name"], "status": status,
                    "latency_ms": latency_ms, "error": err,
                    "reliability": round(_db_mod.get_db().engine_reliability(engine["name"]) or 0, 3)}
        except Exception as exc:
            err = exc.__class__.__name__ + ": " + str(exc)[:100]
            _db_mod.get_db().engine_history_add(engine["name"], "down", None, err)
            return {"name": engine["name"], "status": "down",
                    "latency_ms": None, "error": err,
                    "reliability": round(_db_mod.get_db().engine_reliability(engine["name"]) or 0, 3)}

    results_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_engine = {ex.submit(_ping, eng): eng for eng in SEARCH_ENGINES}
        for fut in as_completed(future_to_engine):
            try:
                r = fut.result()
                results_map[r["name"]] = r
            except Exception:
                continue

    return [results_map.get(e["name"], {"name": e["name"], "status": "down",
             "latency_ms": None, "error": "no result"}) for e in SEARCH_ENGINES]


# ── mode-based engine routing ──────────────────────────────────────
# Known ransomware leak-site .onion addresses (dark.fail verified)
_RANSOMWARE_SEEDS = [
    "http://alphvmmm27o3abo3r2mlmjrpdmzle3rykajqc5xsj7j7ejksbpsa36ad.onion",
    "http://lockbit7ouvrsdgtojeoj5hvu6bljqtghitekwpdy3b6y62ixtsu5jqd.onion",
]

_MODE_ENGINES: dict[str, list[str]] = {
    "threat_intel":      [],  # all engines
    "ransomware":        ["Ahmia", "Tor66", "Excavator", "Ahmia-clearnet"],
    "personal_identity": ["Ahmia", "OnionLand", "Tor66", "DuckDuckGo-Tor", "Ahmia-clearnet"],
    "corporate":         ["Ahmia", "Excavator", "Tor66", "TheDeepSearches", "Ahmia-clearnet"],
}

_MODE_SEEDS: dict[str, list[str]] = {
    "ransomware": _RANSOMWARE_SEEDS,
}


def mode_engines(mode: str) -> Optional[list[str]]:
    """Return recommended engine list for an analysis mode, or None for all.

    Args:
        mode: threat_intel | ransomware | personal_identity | corporate
    """
    engines = _MODE_ENGINES.get(mode, [])
    return engines if engines else None


def mode_seeds(mode: str) -> list[str]:
    """Return known seed .onion URLs for a mode (e.g., ransomware blogs)."""
    return list(_MODE_SEEDS.get(mode, []))


def list_modes() -> dict:
    """Return all modes with their engine configuration."""
    return {
        mode: {
            "engines": _MODE_ENGINES.get(mode, ["all"]),
            "seeds": len(mode_seeds(mode)),
        }
        for mode in _MODE_ENGINES
    }
