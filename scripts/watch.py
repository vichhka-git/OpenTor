#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — watch.py — Minimal Watch Job Checker
================================================
Checks watch jobs defined in a JSON file. The LLM manages job lifecycle
(add/remove/enable/disable) by editing the JSON file directly.

Usage:
    python3 scripts/watch.py check --job-file ./watch_jobs.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Optional

# ── ensure scripts/ is on path ─────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import torcore
import engines as eng
import osint


def _load_jobs(path: str) -> list[dict]:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "jobs" in data:
            return data["jobs"]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def _generate_fingerprint(query: str, mode: Optional[str] = None) -> str:
    results = eng.search(query, mode=mode, use_cache=False, max_results=50)
    raw = "|".join(
        sorted(f"{r.get('url', '')}:::{r.get('title', '')}" for r in results)
    )
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()


def cmd_check(args: argparse.Namespace) -> None:
    jobs = _load_jobs(args.job_file)
    if not jobs:
        print(f"No jobs found in {args.job_file}")
        return

    now = time.time()
    results_list = []

    for job in jobs:
        if not job.get("enabled", True):
            continue

        query = job.get("query", "")
        mode = job.get("mode", "") or None
        jid_short = job.get("id", "?")[:8]

        print(f"Checking [{jid_short}] {query} ...", end=" ")
        sys.stdout.flush()

        try:
            new_fingerprint = _generate_fingerprint(query, mode=mode)
            old_fingerprint = job.get("fingerprint", "")
            is_new = bool(old_fingerprint) and new_fingerprint != old_fingerprint

            job["fingerprint"] = new_fingerprint
            job["last_run"] = now

            if is_new:
                current = eng.search(query, mode=mode, use_cache=False, max_results=50)
                print(f"ALERT: {len(current)} results changed!")
                for i, r in enumerate(current[:5], 1):
                    print(f"  {i}. [{r.get('engine', '?')}] {r.get('title', '')[:80]}")
                results_list.append({
                    "id": job.get("id", ""), "query": query,
                    "status": "new_results", "count": len(current),
                })
            else:
                status = "baseline" if not old_fingerprint else "no_change"
                print(f"OK ({status})")
                results_list.append({
                    "id": job.get("id", ""), "query": query, "status": status,
                })
        except Exception as e:
            print(f"ERROR: {e}")
            results_list.append({
                "id": job.get("id", ""), "query": query, "status": "error",
                "error": str(e),
            })

    if args.json:
        print(json.dumps({"results": results_list}, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenTor Watch Job Checker",
        epilog="Manage jobs by editing the JSON file directly.",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Run all watch jobs and alert on changes")
    p_check.add_argument("--job-file", required=True, help="Path to JSON file with watch jobs")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
