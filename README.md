# OpenTor 🧅

**Tor / Dark Web Access for AI Agents — OpenCode & Claude Code Skill**

OpenTor gives LLMs full access to the Tor network and .onion hidden services.
Not a standalone tool — an **orchestrator-conductor architecture** where the LLM
is the intelligence and the Python modules provide mechanical transport, search,
and entity extraction.

```bash
# Quick start — the LLM runs these:
pip install -r requirements.txt            # install dependencies
python3 scripts/setup.py                   # interactive setup
python3 scripts/opentor.py check           # verify Tor
python3 scripts/opentor.py search "ransomware leak"  # search dark web
```

## Install as a Skill

### Claude Code

```bash
# Clone into Claude Code skills directory
git clone https://github.com/opentor/opentor ~/.claude/skills/open-tor

# Or copy from local
cp -r OpenTor ~/.claude/skills/open-tor

# Run setup (the LLM can do this itself)
cd ~/.claude/skills/open-tor
pip install -r requirements.txt
python3 scripts/setup.py
```

After install, start a **new Claude Code session**. The skill loads automatically
when you ask about dark web topics, .onion URLs, ransomware groups, credential
leaks, or Tor-based OSINT.

### OpenCode

```bash
# Copy to OpenCode skills directory
cp -r OpenTor ~/.config/opencode/skills/open-tor
```

### LLM Self-Install

The orchestrator (Claude) can install itself. Just say:

> "Install OpenTor and set up Tor access"

The LLM will:
1. Clone the repo to the skills directory
2. Run `pip install -r requirements.txt` (asking for sudo/venv choice)
3. Run `python3 scripts/setup.py` for interactive configuration
4. Install and start Tor if not present
5. Verify with `python3 scripts/opentor.py check`

### Standalone (without skill system)

```bash
git clone https://github.com/opentor/opentor
cd opentor
pip install -r requirements.txt
python3 scripts/setup.py
python3 scripts/opentor.py check
```

## Requirements

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Python | 3.10+ | Runtime |
| Tor | any | SOCKS5 proxy (:9050) + ControlPort (:9051) |
| `requests[socks]` | >=2.28 | HTTP through Tor SOCKS5 |
| `beautifulsoup4` | >=4.11 | HTML parsing for search + fetch |
| `python-dotenv` | >=1.0 | .env configuration |
| `stem` | >=1.8 | Tor ControlPort (circuit rotation) |

No LLM API keys required. No external AI service dependencies. The orchestrator
IS the LLM.

## Commands

| Command | What it does |
|---------|-------------|
| `opentor.py check` | Verify Tor is running, show exit IP |
| `opentor.py engines` | Ping 12 search engines, show latency/reliability |
| `opentor.py search "query"` | Search dark web — all engines, scored results |
| `opentor.py fetch "url"` | Fetch any .onion or clearnet URL through Tor |
| `opentor.py renew` | Rotate Tor circuit (new identity) |
| `opentor.py entities --text "..."` | Extract IOCs (emails, crypto, onions, PGP) |
| `opentor.py crawl "url"` | Spider a .onion site — follow links, map structure |
| `opentor.py crawl-export <id>` | Export crawl results |

### Options

```
--mode MODE        threat_intel | ransomware | personal_identity | corporate
--engines NAME     Specific engines (e.g. Ahmia Tor66)
--max N            Max results (default 20)
--format FMT       json (default) | csv | stix | misp | text
--out FILE         Write output to file
--json             Machine-readable JSON output
--depth N          Crawl depth (default 3, for crawl subcommand)
--pages N          Max pages (default 100, for crawl subcommand)
--stay             Stay on same .onion domain (for crawl)
```

## Features

### Dark Web Search
12 verified-live engines queried in parallel through Tor. Results scored by
BM25 relevance, deduplicated across engines, with 30-minute SQLite cache.

### .onion Spider
BFS crawler follows links through .onion sites, extracts entities (emails,
crypto addresses, PGP keys, onion links), builds a link graph, stores
everything in SQLite. The LLM cannot navigate hundreds of Tor URLs — the
spider can.

### Entity Extraction
Regex-based IOC extraction: emails, BTC/XMR/ETH addresses, .onion URLs,
PGP keys, phone numbers, IPs, domains.

### Output Formats
Export results to JSON, CSV, STIX 2.1 Bundle, MISP Event — feed directly
into threat intelligence platforms.

### Analysis Modes
Four modes with engine routing: `threat_intel`, `ransomware`, `personal_identity`, `corporate`.

### SQLite Persistence
Search results cached across sessions. Engine reliability tracked with
exponential time-decay scoring. Crawl data stored for export.

### Content Safety
Automatic blacklist for CSAM and illegal content. Cannot be disabled.

### Clearnet-First Strategy
The skill teaches the LLM to search public internet first (Google/DuckDuckGo)
to understand context before targeting dark web queries — validated in real
ransomware investigations.

### Professional Reporting Standards
Six built-in thinking directives teach the LLM to:
- Label every assertion (`✓ Observed` / `⚡ Inferred` / `❓ Uncertain` / `🤖 AI Analysis`)
- Treat folder names as hypotheses (crawl before reporting)
- Report raw data before interpretation
- Never fill gaps with assumptions
- Make every finding traceable to source evidence

## Roadmap

- **Domain allow/deny lists** — scope control for spider (allowlist specific .onion domains, block others)
- **Export redaction** — strip PII, credentials, and sensitive data from STIX/MISP exports
- **Safe mode** — read-only default (search + fetch only, crawl disabled unless explicitly allowed)
- **Crawl scheduling** — time-boxed spider runs (stop after N minutes regardless of depth/pages)
- **Report templates** — customizable output structure per investigation type

## License

MIT License — see [LICENSE](LICENSE).

Engine catalogue adapted from [Robin](https://github.com/apurvsinghgautam/robin) (MIT).

**Use responsibly.** Built for OSINT, threat intelligence, and security research.
