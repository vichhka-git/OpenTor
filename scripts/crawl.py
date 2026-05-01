#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — crawl.py — Onion Spider
===================================
Depth-first .onion crawler. Follows links, maps site structure, extracts
entities (emails, crypto, PGP, onion links), stores everything in SQLite.

This is pure mechanical traversal — the LLM cannot recursively navigate
hundreds of .onion URLs through Tor. That's what the spider does.

Functions:
    crawl(seed_url, max_depth, max_pages, stay_on_domain, job_id) → CrawlResult
    crawl_export(job_id) → dict

Usage from CLI:
    python3 opentor.py crawl "http://example.onion" --depth 2 --pages 50
    python3 opentor.py crawl-export <job_id>
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urljoin, urlparse

import torcore
import osint as _osint
import db as _db_mod


@dataclasses.dataclass
class CrawlResult:
    """Summary of a completed crawl job."""
    job_id:       str
    seed_url:     str
    pages_found:  int
    links_found:  list[str]
    entities:     dict
    db_path:      str


def crawl(
    seed_url: str,
    max_depth: int = 3,
    max_pages: int = 100,
    stay_on_domain: bool = True,
    extract_entities: bool = True,
    max_workers: int = 4,
    job_id: Optional[str] = None,
) -> CrawlResult:
    """Depth-first .onion spider with concurrent workers.

    Follows links from seed_url, maps site structure, extracts entities,
    and stores everything in SQLite under a single job_id.

    Args:
        seed_url:         Starting .onion URL.
        max_depth:        Max link-follow depth (default 3).
        max_pages:        Hard cap on total pages (default 100).
        stay_on_domain:   Only follow same-host links (default True).
        extract_entities: Run entity extraction per page (default True).
        max_workers:      Concurrent fetch workers (default 4, max 4 for Tor).
        job_id:           Custom job ID for resuming. Auto-generated.

    Returns:
        CrawlResult with pages_found, links_found, entities, job_id.
    """
    if not seed_url.startswith(("http://", "https://")):
        seed_url = "http://" + seed_url

    job_id = job_id or str(uuid.uuid4())[:12]
    db = _db_mod.get_db()
    parsed_seed = urlparse(seed_url)
    seed_host = parsed_seed.netloc

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(seed_url, 0)]
    all_entities: dict = {
        "emails": [], "onion_links": [], "btc_addresses": [],
        "xmr_addresses": [], "eth_addresses": [], "pgp_keys": 0,
        "phones": [], "usernames": [],
    }
    pages_crawled = 0
    links_found: list[str] = []
    _seen_links: set[str] = set()
    _lock = threading.Lock()

    # Record seed URL
    _clean_seed = seed_url.rstrip("/")
    visited.add(_clean_seed)
    _seen_links.add(_clean_seed)
    links_found.append(_clean_seed)

    def _process_page(url: str, depth: int) -> list[tuple[str, int]]:
        nonlocal pages_crawled, links_found
        result = torcore.fetch(url)
        if result.get("error") or not result.get("text"):
            return []

        text = result["text"]
        title = result.get("title", "")

        # Extract entities
        entities = _osint.extract_entities(text) if extract_entities else {}

        # Save to database
        db.crawl_save_page(url, job_id, depth, title, text, entities)

        with _lock:
            pages_crawled += 1
            if extract_entities:
                for k, v in entities.items():
                    if isinstance(v, list):
                        all_entities[k].extend(v)
                    elif isinstance(v, int):
                        all_entities[k] = all_entities.get(k, 0) + v

        # Record all discovered outbound links (for the link graph)
        for link in result.get("links", []):
            href = link.get("href", "")
            if not href:
                continue
            _clean_href = href.rstrip("/")
            with _lock:
                if _clean_href not in _seen_links:
                    _seen_links.add(_clean_href)
                    links_found.append(_clean_href)

        # Collect child links for crawl queue
        child_links: list[tuple[str, int]] = []
        if depth < max_depth:
            for link in result.get("links", []):
                href = link.get("href", "")
                if not href or ".onion" not in href:
                    continue
                if stay_on_domain and urlparse(href).netloc != seed_host:
                    continue
                clean = href.rstrip("/")
                with _lock:
                    if clean not in visited:
                        visited.add(clean)
                        db.crawl_save_link(url, clean)
                        child_links.append((clean, depth + 1))
        return child_links

    # BFS with concurrent workers
    while queue and pages_crawled < max_pages:
        batch = []
        while queue and len(batch) < max_workers:
            batch.append(queue.pop(0))

        with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as ex:
            future_map = {
                ex.submit(_process_page, url, depth): (url, depth)
                for url, depth in batch
            }
            for fut in as_completed(future_map):
                try:
                    children = fut.result()
                    queue.extend(children)
                except Exception:
                    pass

        if pages_crawled >= max_pages:
            break

    # Deduplicate entities
    for k, v in all_entities.items():
        if isinstance(v, list):
            all_entities[k] = sorted(set(v))

    return CrawlResult(
        job_id=job_id,
        seed_url=seed_url,
        pages_found=pages_crawled,
        links_found=links_found,
        entities=all_entities,
        db_path=_db_mod.DB_PATH,
    )


def crawl_export(job_id: str) -> dict:
    """Export all crawled pages and links for a job."""
    return _db_mod.get_db().crawl_export(job_id)
