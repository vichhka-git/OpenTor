#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — osint.py — Orchestrator Intelligence Tools
======================================================
High-level tools for the orchestrator (Claude) to conduct dark web OSINT.
This is the primary interface layer — every function here is designed to be
called via bash from the orchestrator.

The orchestrator is the intelligence. This module provides the tools:
search, scrape, score, extract, format — all mechanical operations that
feed data to the orchestrator for analysis.

Functions:
    search_darkweb(query, engines, max, mode)  → search results with scores
    batch_scrape(urls, max_workers)            → concurrent .onion fetch
    score_results(query, results, texts)       → BM25 relevance ranking
    extract_entities(text)                     → regex IOCs (emails, crypto, onions)
    format_output(results, format, query)      → CSV/JSON/STIX/MISP output
    content_fingerprint(text)                  → MD5 dedup detection
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urlparse

import torcore
import engines as eng

__version__ = torcore.__version__

# ── stopwords for keyword extraction ───────────────────────────────
_STOPWORDS = frozenset({
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


# ── search_darkweb — main entry point ──────────────────────────────
def search_darkweb(
    query: str,
    engines: Optional[list[str]] = None,
    max_results: int = 20,
    mode: Optional[str] = None,
) -> dict:
    """Search the dark web and return scored, deduplicated results.

    This is the primary endpoint for orchestrator-driven investigations.
    The orchestrator calls this, evaluates the results, then decides what
    to scrape next.

    Args:
        query:       Search keywords (natural language OK — use short queries).
        engines:     Specific engine names, or None to auto-select.
        max_results: Cap on unique results.
        mode:        Analysis mode for auto engine selection.

    Returns:
        {
            "query": str,
            "mode": str,
            "engines_used": [str],
            "total_raw": int,
            "results": [{"title","url","engine","confidence"}, ...],
            "search_time_s": float
        }
    """
    t0 = time.time()

    if mode and not engines:
        engines = eng.mode_engines(mode)

    raw = eng.search(query, engines=engines, max_results=max_results, mode=mode)
    scored = score_results(query, raw)

    # Add mode seeds as bonus results
    if mode:
        for seed_url in eng.mode_seeds(mode):
            # Quick HEAD check — if reachable, add as known source
            try:
                sess = torcore.pool_session() if torcore.TOR_POOL_SIZE > 0 else torcore.tor_session()
                resp = sess.head(seed_url, timeout=15)
                if resp.status_code < 500:
                    scored.append({
                        "title": f"[{mode} seed] {seed_url}",
                        "url": seed_url,
                        "engine": "seed",
                        "confidence": 0.85,
                    })
            except Exception:
                pass

    return {
        "query": query,
        "mode": mode or "threat_intel",
        "engines_used": list(set(r.get("engine", "?") for r in scored)),
        "total_raw": len(raw),
        "results": scored[:max_results],
        "search_time_s": round(time.time() - t0, 2),
    }


# ── batch_scrape — concurrent .onion fetch ─────────────────────────
def batch_scrape(urls: list[str], max_workers: int = 5) -> dict[str, dict]:
    """Fetch multiple URLs concurrently through Tor.

    Args:
        urls:        List of URLs to fetch.
        max_workers: Parallel threads (capped at 5 for Tor).

    Returns:
        {url_str: {"title": str, "text": str, "error": str|None}, ...}
    """
    def _fetch_one(url: str) -> tuple[str, dict]:
        try:
            result = torcore.fetch(url)
            return url, result
        except Exception as e:
            return url, {"title": None, "text": "", "error": str(e)}

    results = {}
    workers = min(max_workers, len(urls)) if urls else 1
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_one, u): u for u in urls}
        for fut in as_completed(futures):
            try:
                url, data = fut.result()
                results[url] = data
            except Exception:
                continue
    return results


# ── BM25-lite relevance scoring ────────────────────────────────────
def score_results(
    query: str,
    results: list[dict],
    page_texts: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Score results by keyword overlap with query using BM25-lite.

    Pure stdlib — no external dependencies. Adds a "confidence" score
    (0.0–1.0) to each result dict. Results with no shared terms still
    get a baseline score (0.05) since a search engine returned them.

    Args:
        query:      The search query string.
        results:    List of result dicts with "title" and "url" keys.
        page_texts: Optional {url: text} for deeper content scoring.

    Returns:
        Same dicts with "confidence" added, sorted best-first.
    """
    if not results:
        return []

    if isinstance(query, list):
        query = " ".join(str(t) for t in query)

    q_terms = set(re.findall(r"[a-z0-9]{3,}", query.lower())) - _STOPWORDS
    if not q_terms:
        for r in results:
            r.setdefault("confidence", 0.5)
        return results

    k1, b = 1.5, 0.75
    scored = []

    for result in results:
        page_text = (page_texts or {}).get(result.get("url", ""), "")[:2000]
        doc = " ".join(filter(None, [
            result.get("title", ""),
            result.get("snippet", "") or result.get("description", ""),
            result.get("url", ""),
            page_text,
        ])).lower()

        doc_terms = re.findall(r"[a-z0-9]{3,}", doc)
        term_count = {t: doc_terms.count(t) for t in q_terms if t in doc_terms}
        dl = max(len(doc_terms), 1)
        avgdl = 12.0

        score = sum(
            (cnt * (k1 + 1)) / (cnt + k1 * (1 - b + b * dl / avgdl))
            for cnt in term_count.values()
        )
        confidence = max(min(score / (len(q_terms) * 2 + 1), 1.0), 0.05)

        r_copy = dict(result)
        r_copy["confidence"] = round(confidence, 4)
        scored.append((confidence, r_copy))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored]


# ── entity extraction ──────────────────────────────────────────────
def extract_entities(text: str) -> dict:
    """Extract structured intelligence entities from raw text.

    Finds: emails, .onion URLs, BTC/XMR/ETH addresses, PGP keys,
    phone numbers, IPs, domains.

    Args:
        text: Raw text from a fetched page or combined content.

    Returns:
        {
            "emails": [str], "onion_links": [str],
            "btc_addresses": [str], "xmr_addresses": [str],
            "eth_addresses": [str], "pgp_keys": bool,
            "phones": [str], "ips": [str], "domains": [str]
        }
    """
    return {
        "emails": sorted(set(
            re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)
        )),
        "onion_links": sorted(set(
            re.findall(r"https?://[a-z2-7]{16,56}\.onion(?:/[^\s\"'<>]*)?", text)
        )),
        "btc_addresses": sorted(set(
            re.findall(r"\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}\b", text)
        )),
        "xmr_addresses": sorted(set(
            re.findall(r"\b4[0-9AB][0-9a-zA-Z]{93}\b", text)
        )),
        "eth_addresses": sorted(set(
            re.findall(r"\b0x[a-fA-F0-9]{40}\b", text)
        )),
        "pgp_keys": bool(re.search(r"BEGIN PGP|END PGP|-----BEGIN", text)),
        "phones": sorted(set(
            re.findall(r"\+\d{1,3}[\s-]?\d{3,}[\s-]?\d{3,}[\s-]?\d{3,}", text)
        )),
        "ips": sorted(set(
            re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        )),
        "domains": sorted(set(
            re.findall(r"(?:https?://)?([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}", text)
        )),
    }


# ── content deduplication ──────────────────────────────────────────
def content_fingerprint(text: str) -> str:
    """Generate a content fingerprint for near-duplicate detection."""
    normalised = re.sub(r"\s+", " ", text.lower()).strip()
    normalised = re.sub(r"(copy|copyright|all rights reserved|terms|privacy)[^.]*\.", "", normalised)
    return hashlib.md5(normalised[:4096].encode(), usedforsecurity=False).hexdigest()


# ── output formatting ──────────────────────────────────────────────
def format_output(
    results: list[dict],
    fmt: str = "json",
    query: str = "",
    report_text: str = "",
) -> str:
    """Format search/scrape results for export.

    Args:
        results:     List of {title, url, engine, confidence} dicts.
        fmt:         Output format: json | csv | stix | misp | text.
        query:       Investigation query (for report headers).
        report_text: Full OSINT report text (for STIX/MISP embedding).

    Returns:
        Formatted string ready for file output.
    """
    if fmt == "csv":
        import io, csv
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["title", "url", "engine", "confidence"],
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        for r in results:
            writer.writerow({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "engine": r.get("engine", ""),
                "confidence": r.get("confidence", ""),
            })
        return buf.getvalue()

    if fmt == "stix":
        import uuid
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        objects = []

        identity_id = f"identity--{uuid.uuid4()}"
        objects.append({
            "type": "identity", "spec_version": "2.1", "id": identity_id,
            "created": ts, "modified": ts,
            "name": f"OpenTor v{__version__}",
            "identity_class": "system",
        })

        report_id = f"report--{uuid.uuid4()}"
        object_refs = [identity_id]

        for r in results[:50]:
            url_val = r.get("url", "")
            if not url_val:
                continue
            url_id = f"url--{uuid.uuid4()}"
            objects.append({"type": "url", "spec_version": "2.1", "id": url_id,
                            "value": url_val})
            objects.append({
                "type": "relationship", "spec_version": "2.1",
                "id": f"relationship--{uuid.uuid4()}",
                "created": ts, "modified": ts,
                "relationship_type": "related-to",
                "source_ref": report_id, "target_ref": url_id,
            })
            object_refs.append(url_id)

        objects.insert(1, {
            "type": "report", "spec_version": "2.1", "id": report_id,
            "created": ts, "modified": ts,
            "name": f"OpenTor OSINT: {query or 'Investigation'}",
            "published": ts,
            "created_by_ref": identity_id,
            "object_refs": object_refs,
        })

        if report_text:
            note_id = f"note--{uuid.uuid4()}"
            objects.append({
                "type": "note", "spec_version": "2.1", "id": note_id,
                "created": ts, "modified": ts,
                "abstract": f"OpenTor OSINT Report: {query}",
                "content": report_text[:20000],
                "object_refs": [report_id],
            })
            object_refs.append(note_id)

        bundle = {
            "type": "bundle", "id": f"bundle--{uuid.uuid4()}",
            "spec_version": "2.1", "objects": objects,
        }
        return json.dumps(bundle, indent=2)

    if fmt == "misp":
        import uuid
        ts = str(int(time.time()))
        date_str = time.strftime("%Y-%m-%d", time.gmtime())
        event_uuid = str(uuid.uuid4())

        attributes = []
        for r in results[:50]:
            url_val = r.get("url", "")
            if not url_val:
                continue
            conf = r.get("confidence", 0) or 0
            comment = f"[engine: {r.get('engine', '?')}] [confidence: {conf:.4f}] {r.get('title', '')[:120]}"
            attributes.append({
                "uuid": str(uuid.uuid4()), "type": "url",
                "category": "External analysis", "value": url_val,
                "comment": comment, "to_ids": False,
                "distribution": "0", "timestamp": ts,
            })
            hostname = urlparse(url_val).hostname or ""
            if hostname:
                attr_type = "domain" if "." in hostname else "hostname"
                attributes.append({
                    "uuid": str(uuid.uuid4()), "type": attr_type,
                    "category": "Network activity", "value": hostname,
                    "comment": f"Extracted from: {r.get('title', '')[:80]}",
                    "to_ids": True, "distribution": "0", "timestamp": ts,
                })

        if report_text:
            attributes.append({
                "uuid": str(uuid.uuid4()), "type": "comment",
                "category": "Attribution", "value": report_text[:65536],
                "comment": f"OpenTor v{__version__} OSINT report",
                "to_ids": False, "distribution": "0", "timestamp": ts,
            })

        if query:
            attributes.append({
                "uuid": str(uuid.uuid4()), "type": "text",
                "category": "Other", "value": query,
                "comment": "Original investigation query",
                "to_ids": False, "distribution": "0", "timestamp": ts,
            })

        return json.dumps({
            "Event": {
                "uuid": event_uuid,
                "info": f"OpenTor OSINT: {query or 'Dark web investigation'}",
                "date": date_str, "timestamp": ts,
                "threat_level_id": "2", "analysis": "1",
                "distribution": "0", "published": False,
                "Attribute": attributes,
                "Tag": [
                    {"name": "tlp:amber"}, {"name": "dark-web"},
                    {"name": "osint"}, {"name": f'opentor:version="{__version__}"'},
                ],
            }
        }, indent=2)

    if fmt == "text":
        lines = []
        for i, r in enumerate(results, 1):
            conf = f"  conf={r.get('confidence', 0):.2f}" if "confidence" in r else ""
            lines.append(f"{i:>3}. [{r.get('engine', '?')}]{conf}  {r.get('title', '')[:80]}")
            lines.append(f"      {r.get('url', '')}")
        return "\n".join(lines)

    # default: JSON
    return json.dumps(results, indent=2, default=str)
