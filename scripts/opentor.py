#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — opentor.py — Unified CLI Entry Point
================================================
Dark web OSINT toolkit. Wraps torcore, engines, and osint into a clean
argparse-based CLI with subcommands.

Usage:
    opentor.py check
    opentor.py engines
    opentor.py search "query"
    opentor.py fetch "url"
    opentor.py renew
    opentor.py entities --text "..."  (or --file, or stdin)
    opentor.py --version
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import os
import sys
import time
from typing import Callable, Optional

# ── ensure sibling modules are importable ───────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

# ── load .env from parent OpenTor directory ─────────────────────────
try:
    from dotenv import load_dotenv

    _env_path = os.path.join(os.path.dirname(_SCRIPT_DIR), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=False)
except ImportError:
    pass

import torcore
import engines as eng
import osint

__version__ = torcore.__version__

# ── constants ───────────────────────────────────────────────────────
_SUBCOMMANDS = ["check", "engines", "search", "fetch", "renew", "entities", "crawl", "crawl-export", "stats"]

# ── output helpers ──────────────────────────────────────────────────


def _hint_permission(error_text: str) -> None:
    """Print a permission/cookie/auth hint if applicable."""
    lower = error_text.lower()
    if any(kw in lower for kw in ("permission denied", "cookie", "auth")):
        print(
            "Hint: This may need sudo or cookie auth fix. Ask user: 'I need "
            "sudo access. What's your sudo password?'"
        )


def emit_output(
    json_data: object,
    human_text: str,
    args: argparse.Namespace,
) -> None:
    """Write output to stdout and/or file based on flags.

    - Default: human-readable to stdout.
    - ``--json``: JSON to stdout only (no human output on stdout).
    - ``--out``: write (JSON or human) to file in addition to stdout.
    """
    if args.json:
        out = json.dumps(json_data, indent=2, default=str)
        print(out)
    else:
        print(human_text)

    out_path: Optional[str] = getattr(args, "out", None)
    if out_path:
        with open(out_path, "w") as f:
            if args.json:
                json.dump(json_data, f, indent=2, default=str)
            else:
                f.write(human_text)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


# ── permission-aware error wrapper ──────────────────────────────────


def safe_call(fn: Callable, *args, **kwargs):
    """Call *fn*, catch exceptions, print clean error, exit 1."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        print(f"✗  Error: {msg}")
        _hint_permission(msg)
        sys.exit(1)


# ── subcommand: check ────────────────────────────────────────────────


def cmd_check(args: argparse.Namespace) -> None:
    print("Checking Tor connectivity ... ", end="", flush=True)
    result = safe_call(torcore.check_tor)
    print("done.\n")

    active = result.get("tor_active", False)
    ip = result.get("exit_ip", None)
    err = result.get("error", None)

    if not active:
        if err and "not reachable" in err:
            print(
                "✗  Tor is NOT running.\n"
                "   Cannot reach SOCKS port. Start Tor:\n"
                "     sudo systemctl start tor   (Linux)\n"
                "     tor                        (manual)"
            )
        else:
            print(f"✗  Tor is NOT active.  Exit IP: {ip or 'unknown'}")
            if err:
                print(f"   Error: {err}")
        sys.exit(1)

    print(f"✓  Tor is ACTIVE")
    print(f"   Exit IP: {ip or 'unknown'}")

    if args.json:
        emit_output(
            {"tor_active": active, "exit_ip": ip, "error": err},
            "",
            args,
        )


# ── subcommand: engines ──────────────────────────────────────────────


def cmd_engines(args: argparse.Namespace) -> None:
    n = len(eng.SEARCH_ENGINES)
    print(f"Pinging {n} search engines via Tor (~30 sec) ... ", end="", flush=True)
    results = safe_call(eng.check_engines)
    print("done.\n")

    alive = [r for r in results if r["status"] == "up"]
    dead = [r for r in results if r["status"] == "down"]

    lines: list[str] = []
    for r in results:
        if r["status"] == "up":
            lat = r.get("latency_ms")
            lat_str = f"{lat}ms" if lat is not None else "—"
            lines.append(f"  ✓  {r['name']:<20} {lat_str:>8}")
        else:
            err = r.get("error") or "unknown"
            lines.append(f"  ✗  {r['name']:<20} {err}")

    print("\n".join(lines))
    total = len(results)
    print(f"\n  Alive: {len(alive)}/{total}   Dead: {len(dead)}/{total}")

    if alive:
        print(f"  Alive engines: {', '.join(r['name'] for r in alive)}")

    if args.json:
        emit_output(results, "", args)


# ── subcommand: search ───────────────────────────────────────────────


def _build_search_table(results: list[dict], query: str, time_s: float, engines_used: list[str]) -> str:
    lines = [
        f'Results for "{query}" '
        f"({_plural(len(engines_used), 'engine')}, {time_s:.2f}s):\n"
    ]
    for i, r in enumerate(results, 1):
        conf = r.get("confidence", 0)
        engine = r.get("engine", "?")
        title = (r.get("title") or "(no title)")[:100]
        url = r.get("url", "")
        lines.append(f"  {i:>3}. [{engine}]  {conf:.4f}  {title}")
        lines.append(f"       {url}")
    if not results:
        lines.append("  (no results)")
    return "\n".join(lines)


def cmd_search(args: argparse.Namespace) -> None:
    engines_opt: Optional[list[str]] = args.engines if getattr(args, "engines", None) else None
    mode: Optional[str] = getattr(args, "mode", None)
    max_results: int = getattr(args, "max", 20)

    engine_count = len(engines_opt) if engines_opt else len(eng.SEARCH_ENGINES)

    print(
        f'Searching {engine_count} dark web engines for: "{args.query}" '
        "... ",
        end="",
        flush=True,
    )
    result = safe_call(
        osint.search_darkweb,
        args.query,
        engines=engines_opt,
        max_results=max_results,
        mode=mode,
    )
    print("done.\n")

    results = result.get("results", [])
    search_time = result.get("search_time_s", 0)
    engines_used = result.get("engines_used", [])

    human = _build_search_table(results, args.query, search_time, engines_used)

    # --format: override output with osint.format_output (unless --json)
    fmt: Optional[str] = getattr(args, "format", None)
    if fmt and not args.json:
        formatted = safe_call(
            osint.format_output, results, fmt=fmt, query=args.query
        )
        human = formatted

    json_data = result  # full dict for JSON output

    if args.json:
        emit_output(json_data, human, args)
    else:
        print(human)


# ── subcommand: fetch ────────────────────────────────────────────────


def _ensure_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def cmd_fetch(args: argparse.Namespace) -> None:
    url = _ensure_url(args.url)

    print(f"Fetching {url} through Tor ... ", end="", flush=True)
    result = safe_call(torcore.fetch, url)
    print("done.\n")

    title = result.get("title") or "(no title)"
    status = result.get("status", 0)
    text = result.get("text", "")
    links = result.get("links", [])
    error = result.get("error")
    is_onion = result.get("is_onion", False)
    truncated = result.get("truncated", False)

    if status == 0:
        print(f"✗  Offline: {url}")
        if is_onion:
            print("   This .onion site appears to be offline or unreachable.")
        if error:
            print(f"   Error: {error}")
        sys.exit(1)

    lat_str = "✓" if status and status < 400 else "⚠"
    print(f"  {lat_str}  Status: {status}")
    print(f"  Title: {title}")
    print(f"  Content ({len(text)} chars):")
    print(f"  {'─' * 60}")
    print(text[:4000])
    if truncated or len(text) > 4000:
        print(f"  … (truncated, full length: {len(text)} chars)")
    print(f"  {'─' * 60}")
    print(f"  Links found: {len(links)}")

    if getattr(args, "links", False) and links:
        print(f"\n  All links ({len(links)}):")
        for lnk in links:
            href = lnk.get("href", "")
            txt = lnk.get("text", "")[:60]
            print(f"    • {txt}")
            print(f"      {href}")

    if args.json:
        emit_output(result, "", args)


# ── subcommand: renew ────────────────────────────────────────────────


def cmd_renew(args: argparse.Namespace) -> None:
    print("Rotating Tor identity (NEWNYM) ... ", end="", flush=True)
    result = safe_call(torcore.renew_identity)
    print("done.\n")

    success = result.get("success", False)
    error = result.get("error")

    if success:
        print("✓  Tor identity rotated successfully")
    else:
        print(f"✗  Failed to rotate Tor identity")
        if error:
            if "stem not installed" in error:
                print("   Install stem: pip install stem")
            elif "auth" in error.lower():
                print(
                    "   ControlPort authentication failed.\n"
                    "   To fix, add to /etc/tor/torrc:\n"
                    "     ControlPort 9051\n"
                    "     HashedControlPassword $(tor --hash-password 'your_password')\n"
                    "   Or set TOR_CONTROL_PASSWORD in .env"
                )
            else:
                print(f"   Error: {error}")
        sys.exit(1)

    if args.json:
        emit_output(result, "", args)


# ── subcommand: entities ─────────────────────────────────────────────


def _read_entities_input(args: argparse.Namespace) -> str:
    file_path: Optional[str] = getattr(args, "file", None)
    text: Optional[str] = getattr(args, "text", None)

    if file_path:
        try:
            with open(file_path, "r") as f:
                return f.read()
        except FileNotFoundError:
            print(f"✗  File not found: {file_path}")
            sys.exit(1)
        except Exception as e:
            print(f"✗  Error reading file: {e}")
            sys.exit(1)

    if text:
        return text

    # stdin
    if not sys.stdin.isatty():
        return sys.stdin.read()

    print("✗  No input provided. Use --file PATH, --text '...', or pipe data.")
    sys.exit(1)


def cmd_entities(args: argparse.Namespace) -> None:
    text = _read_entities_input(args)

    print("Extracting entities ... ", end="", flush=True)
    entities = safe_call(osint.extract_entities, text)
    print("done.\n")

    human_parts: list[str] = []
    for cat, items in entities.items():
        if isinstance(items, list) and items:
            human_parts.append(f"  {cat.upper()} ({len(items)} found):")
            for item in items[:20]:
                human_parts.append(f"    • {item}")
            if len(items) > 20:
                human_parts.append(f"    … and {len(items) - 20} more")
            human_parts.append("")
        elif isinstance(items, bool) and items:
            human_parts.append(f"  {cat.upper()}: present\n")

    if not any(
        (isinstance(v, list) and v) or (isinstance(v, bool) and v)
        for v in entities.values()
    ):
        human_parts.append("  No entities found.\n")

    human = "\n".join(human_parts)

    emit_output(entities, human, args)


# ── argparse setup ───────────────────────────────────────────────────


class _FriendlyArgumentParser(argparse.ArgumentParser):
    """Custom parser that suggests close matches for misspelled subcommands."""

    def error(self, message: str):  # type: ignore[override]
        # Check if this looks like a subcommand misspelling
        ctx = "argument subcommand: invalid choice" if "subcommand" in message else ""
        if ctx or "choose from" in message:
            # Try to extract the bad subcommand from the message
            import re as _re

            match = _re.search(r"'([^']+)'", message)
            if match:
                bad = match.group(1)
                close = difflib.get_close_matches(bad, _SUBCOMMANDS, n=3, cutoff=0.4)
                if close:
                    suggestion = ", ".join(close)
                    print(
                        f"✗  Unknown subcommand: '{bad}'.\n"
                        f"   Did you mean: {suggestion}?\n"
                        f"   Use --help for available commands."
                    )
                    sys.exit(2)

        super().error(message)


# ── subcommand: stats ─────────────────────────────────────────────────


def cmd_stats(args: argparse.Namespace) -> None:
    """Show database statistics."""
    sys.path.insert(0, _SCRIPT_DIR)
    import db as _db

    db = _db.get_db()
    c = db._conn()

    print(f"Database: {db._path}\n")

    # Cache stats
    rows = c.execute(
        "SELECT cache_type, COUNT(*) as count FROM cache GROUP BY cache_type"
    ).fetchall()
    print("── Cache ──")
    if rows:
        for r in rows:
            print(f"  {r['cache_type']:<15} {r['count']} entries")
    else:
        print("  (empty)")

    # Engine history
    n = c.execute("SELECT COUNT(*) FROM engine_history").fetchone()[0]
    engines = c.execute(
        "SELECT DISTINCT engine FROM engine_history ORDER BY engine"
    ).fetchall()
    print(f"\n── Engine History ──")
    print(f"  {n} records across {len(engines)} engines")
    for e in engines[:6]:
        rel = db.engine_reliability(e["engine"])
        rel_str = f"{rel:.0%}" if rel is not None else "no data"
        print(f"    {e['engine']:<25} reliability={rel_str}")

    # Crawl stats
    pages = c.execute("SELECT COUNT(*) FROM crawl_pages").fetchone()[0]
    links = c.execute("SELECT COUNT(*) FROM crawl_links").fetchone()[0]
    jobs = c.execute(
        "SELECT job_id, COUNT(*) as count FROM crawl_pages GROUP BY job_id"
    ).fetchall()
    print(f"\n── Crawl Data ──")
    print(f"  {pages} pages, {links} links")
    if jobs:
        for j in jobs:
            print(f"    job {j['job_id']}: {j['count']} pages")

    if args.json:
        print(f"\nDB path: {db._path}")
        print(f"File size: {os.path.getsize(db._path):,} bytes")


# ── subcommand: crawl ────────────────────────────────────────────────


def cmd_crawl(args: argparse.Namespace) -> None:
    """Spider a .onion site."""
    sys.path.insert(0, _SCRIPT_DIR)
    import crawl as _crawl

    url = args.url
    depth = getattr(args, "depth", 3)
    max_pages = getattr(args, "max_pages", 100)
    stay = getattr(args, "stay_on_domain", True)
    use_json = getattr(args, "json", False)

    if not url.startswith("http"):
        url = "http://" + url

    if not use_json:
        print(f"Spidering {url} (depth={depth}, max_pages={max_pages})...")

    result = _crawl.crawl(url, max_depth=depth, max_pages=max_pages, stay_on_domain=stay)

    if use_json:
        print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    else:
        print(f"\n  Job ID : {result.job_id}")
        print(f"  Pages  : {result.pages_found} crawled")
        print(f"  Links  : {len(result.links_found)} discovered")
        entities = result.entities
        if entities:
            print(f"  Entities found:")
            for k, v in entities.items():
                if isinstance(v, list) and v:
                    print(f"    {k}: {len(v)} ({', '.join(v[:5])}{'...' if len(v) > 5 else ''})")
                elif v:
                    print(f"    {k}: {v}")
        print(f"\n  Export: python3 {__file__} crawl-export {result.job_id}")


def cmd_crawl_export(args: argparse.Namespace) -> None:
    """Export crawl results."""
    sys.path.insert(0, _SCRIPT_DIR)
    import crawl as _crawl

    job_id = args.job_id
    use_json = getattr(args, "json", False)
    data = _crawl.crawl_export(job_id)

    if use_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        pages = data.get("pages", [])
        links = data.get("links", [])
        print(f"Job: {job_id}  |  Pages: {len(pages)}  |  Links: {len(links)}")
        for p in pages[:10]:
            print(f"  [{p.get('depth', '?')}] {p.get('title', '(no title)')[:60]}  —  {p.get('url', '')[:60]}")
        if len(pages) > 10:
            print(f"  ... +{len(pages) - 10} more pages")


def _build_parser() -> _FriendlyArgumentParser:
    parser = _FriendlyArgumentParser(
        prog="opentor.py",
        description="OpenTor — Dark Web OSINT Toolkit",
        epilog="Report issues at github.com/your-org/OpenTor",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"OpenTor {__version__}",
        help="Show version and exit",
    )

    sub = parser.add_subparsers(dest="subcommand", required=True, help="Available subcommands")

    # check
    p_check = sub.add_parser("check", help="Verify Tor connectivity")
    p_check.add_argument("--json", action="store_true", help="JSON output")
    p_check.set_defaults(func=cmd_check)

    # engines
    p_eng = sub.add_parser("engines", help="Check dark web search engine health")
    p_eng.add_argument("--json", action="store_true", help="JSON output")
    p_eng.set_defaults(func=cmd_engines)

    # search
    p_search = sub.add_parser("search", help="Search the dark web")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument(
        "--mode",
        choices=["threat_intel", "ransomware", "personal_identity", "corporate"],
        default=None,
        help="Analysis mode for engine selection",
    )
    p_search.add_argument(
        "--engines",
        nargs="+",
        default=None,
        help="Specific engines to search (space-separated names)",
    )
    p_search.add_argument("--max", type=int, default=20, help="Max results (default 20)")
    p_search.add_argument(
        "--format",
        choices=["json", "csv", "stix", "misp", "text"],
        default=None,
        help="Output format (default: human-readable table)",
    )
    p_search.add_argument("--out", help="Write output to file")
    p_search.add_argument("--json", action="store_true", help="JSON output")
    p_search.set_defaults(func=cmd_search)

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch a URL through Tor")
    p_fetch.add_argument("url", help="URL to fetch (http:// is auto-prepended if missing)")
    p_fetch.add_argument("--links", action="store_true", help="Show all extracted links")
    p_fetch.add_argument("--json", action="store_true", help="JSON output")
    p_fetch.set_defaults(func=cmd_fetch)

    # renew
    p_renew = sub.add_parser("renew", help="Rotate Tor identity (NEWNYM)")
    p_renew.add_argument("--json", action="store_true", help="JSON output")
    p_renew.set_defaults(func=cmd_renew)

    # entities
    p_ent = sub.add_parser("entities", help="Extract IOCs/entities from text")
    p_ent.add_argument("--file", help="Read text from file")
    p_ent.add_argument("--text", help="Read text from argument")
    p_ent.add_argument("--json", action="store_true", help="JSON output")
    p_ent.set_defaults(func=cmd_entities)

    # crawl
    p_crawl = sub.add_parser("crawl", help="Spider a .onion site (follow links, extract entities)")
    p_crawl.add_argument("url", help="Starting .onion URL")
    p_crawl.add_argument("--depth", type=int, default=3, help="Max link-follow depth (default: 3)")
    p_crawl.add_argument("--pages", type=int, default=100, dest="max_pages", help="Max pages to fetch (default: 100)")
    p_crawl.add_argument("--stay", action="store_true", dest="stay_on_domain", help="Stay on same .onion domain")
    p_crawl.add_argument("--json", action="store_true", help="JSON output")
    p_crawl.set_defaults(func=cmd_crawl)

    # crawl-export
    p_ce = sub.add_parser("crawl-export", help="Export crawl results by job ID")
    p_ce.add_argument("job_id", help="Crawl job ID")
    p_ce.add_argument("--json", action="store_true", help="JSON output")
    p_ce.set_defaults(func=cmd_crawl_export)

    # stats
    p_stats = sub.add_parser("stats", help="Show database statistics")
    p_stats.add_argument("--json", action="store_true", help="Show file path + size")
    p_stats.set_defaults(func=cmd_stats)

    return parser


# ── main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = _build_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if hasattr(args, "func"):
        try:
            args.func(args)
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            print(f"✗  Unhandled error: {msg}")
            _hint_permission(msg)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
