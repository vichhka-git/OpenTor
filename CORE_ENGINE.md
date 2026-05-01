# OpenTor Core Engine API Reference

## torcore.py — Tor Transport Layer

Pure mechanical Tor operations. No intelligence, no analysis, no search engine logic — just routing traffic through the Tor network.

**File:** `scripts/torcore.py`
**Version:** 1.0.0
**Dependencies:** `pip install requests[socks] beautifulsoup4 stem python-dotenv`
**Requires:** Tor running locally (socks://127.0.0.1:9050)

---

### `tor_session(port=9050) -> requests.Session`

Build a `requests.Session` that routes all traffic through Tor SOCKS5 proxy.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `port` | `int` | `TOR_SOCKS_PORT` (env, default `9050`) | SOCKS5 port. Override for TorPool multi-circuit setups. |

**Returns:**
`requests.Session` with SOCKS5 proxy configured (`socks5h://127.0.0.1:{port}`), retry adapter (2 retries, backoff 0.5s, status 500/502/503/504), and rotating user agent.

**Example:**
```python
from scripts.torcore import tor_session
sess = tor_session()
resp = sess.get("http://example.onion", timeout=30)
```

---

### `check_tor() -> dict`

Verify Tor is running and confirm the exit node by querying `https://check.torproject.org/api/ip`.

**Returns:**

```json
{
  "tor_active": true,
  "exit_ip": "185.220.101.x",
  "error": null
}
```

- `tor_active` (`bool`): `True` if the Tor check API confirms Tor routing.
- `exit_ip` (`str` or `None`): The current Tor exit node IP address.
- `error` (`str` or `None`): Error message if Tor is unreachable or check fails.

If the SOCKS port is not reachable, returns `tor_active: False` immediately with a descriptive error.

**Example:**
```python
from scripts.torcore import check_tor
import json
r = check_tor()
print(json.dumps(r, indent=2))
# {"tor_active": true, "exit_ip": "185.220.101.x", "error": null}
```

---

### `renew_identity() -> dict`

Rotate the Tor circuit to obtain a new exit node by issuing a `NEWNYM` signal. Authentication order: `TOR_CONTROL_PASSWORD` env var → cookie from `TOR_DATA_DIR` → common cookie paths (`/tmp/tor_data/`, `/var/lib/tor/`, `/run/tor/`, `~/.tor/`) → null/empty password fallback.

**Returns:**

```json
{
  "success": true,
  "error": null
}
```

- `success` (`bool`): `True` if the circuit was rotated successfully.
- `error` (`str` or `None`): Error message on failure (e.g., "stem not installed", "Control port auth failed", connection refused).

**Requires:** Tor ControlPort (default 9051) and `pip install stem`.

**Example:**
```python
from scripts.torcore import renew_identity
import json
r = renew_identity()
print(json.dumps(r, indent=2))
```

---

### `fetch(url, max_chars=8000) -> dict`

Fetch any URL through Tor — clearnet or `.onion` hidden service. Attempts HTTPS first, then HTTP fallback for `.onion` URLs. Includes HTML parsing (via BeautifulSoup), title extraction, link extraction (up to 80 links), and content safety filtering.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | — | URL to fetch. If no scheme is provided, `http://` is prepended. |
| `max_chars` | `int` | `OPENTOR_MAX_CHARS` (env, default `8000`) | Maximum characters to return in the `text` field. |

**Returns:**

```json
{
  "url": "http://example.onion/page",
  "is_onion": true,
  "status": 200,
  "title": "Example Hidden Service",
  "text": "Page content truncated to max_chars...",
  "links": [
    {"text": "Link text", "href": "http://other.onion/page"}
  ],
  "truncated": false,
  "error": null
}
```

- `url` (`str`): Final URL after any redirects.
- `is_onion` (`bool`): Whether the original URL was a `.onion` address.
- `status` (`int`): HTTP status code, or `0` if the request failed entirely (hidden service offline).
- `title` (`str` or `None`): Page `<title>` content, or `"[blocked]"` if blacklisted.
- `text` (`str`): Page body text (scripts, styles, and noscript tags removed). Truncated to `max_chars`.
- `links` (`list[dict]`): Up to 80 extracted links with `{"text", "href"}`. Relative paths resolved to absolute.
- `truncated` (`bool`): Whether the page text was longer than `max_chars`.
- `error` (`str` or `None`): Human-readable error message on failure, including safety blocks.

**Security features:**
- Blocks `.onion` → clearnet redirects (de-anonymization protection).
- Content safety filter runs on URL, title, and body text.
- Friendly error messages for common Tor failures (SOCKS, timeout, connection refused, TLS).

**Example:**
```python
from scripts.torcore import fetch
import json
r = fetch("http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion", max_chars=5000)
print(json.dumps(r, indent=2))
```

---

### `TorPool` — Multi-Circuit Pool (Optional)

Spawn multiple independent Tor processes for parallel scraping. Each circuit gets a unique exit identity on consecutive SOCKS ports (9060, 9061, …). Enabled when `OPENTOR_POOL_SIZE > 0` (env var).

**Class:** `TorPool`

**Constructor:**
```python
TorPool(size: int = OPENTOR_POOL_SIZE, base_port: int = POOL_BASE_PORT)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `size` | `int` | `OPENTOR_POOL_SIZE` (env, default `0`) | Number of Tor processes to spawn. `0` disables the pool. |
| `base_port` | `int` | `POOL_BASE_PORT` (env, default `9060`) | Base SOCKS port. Circuit i → `base_port + i`. Control ports → `base_port + 100 + i`. |

**Methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `start()` | `() -> None` | Spawn `size` Tor processes with individual torrc files (temp dirs). Waits up to 30s for all circuits to be ready. |
| `stop()` | `() -> None` | Terminate all Tor processes and clean up temp directories. |
| `session()` | `() -> requests.Session` | Get a session for the next circuit in round-robin order. Falls back to `tor_session()` if pool not running. |
| `renew_all()` | `() -> list[dict]` | Rotate all pool circuits via `NEWNYM`. Returns per-circuit results `{"port", "success", "error"}`. |

**Module-level convenience:**
```python
from scripts.torcore import pool_session
sess = pool_session()  # pool if active, else standard tor_session()
```

**Example:**
```python
from scripts.torcore import TorPool
pool = TorPool(size=3)
pool.start()
sess1 = pool.session()
sess2 = pool.session()
# Use sessions for parallel requests...
pool.stop()
```

---

### `is_content_safe(text) -> bool`

Return `False` if `text` contains any blacklisted phrase or token pair (case-insensitive). This is the core content safety filter — it runs automatically on all fetched content and search results and **cannot be disabled**.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `text` | `str` | Text to scan for blacklisted content. |

**Returns:**
- `True` — content is safe, no blacklist matches.
- `False` — content matches safety blacklist.

**Detection layers:**
1. **Keyword blacklist** — `frozenset` of known CSAM/illegal-content terms.
2. **Token-pair matching** — Catches evasive titles where blacklisted words are separated across tokens.
3. **Context-sensitive "rape" detection** — Blocked only when appearing alongside sexual/minor/distribution context words.

**Example:**
```python
from scripts.torcore import is_content_safe
print(is_content_safe("Legitimate news about security")    # True
print(is_content_safe("child porn dark web"))              # False
```

---

### `pool_session() -> requests.Session`

Get a `requests.Session` — from the TorPool if active (`OPENTOR_POOL_SIZE > 0`), otherwise a standard `tor_session()`. This is the recommended way to get sessions for automated workflows.

**Example:**
```python
from scripts.torcore import pool_session
sess = pool_session()
resp = sess.get("http://example.onion", timeout=30)
```

---

## engines.py — Search Engine Layer

Manages 12 verified-live `.onion` search engines. Handles querying, health checks, mode-based engine routing, seed URLs, and result deduplication.

**File:** `scripts/engines.py`
**Version:** 1.0.0 (matches torcore)

Engine catalogue adapted from [Robin](https://github.com/apurvsinghgautam/robin) (MIT). `.onion` addresses verified via dark.fail.

---

### `SEARCH_ENGINES` — list of 12 engine configs

A `list[dict]` where each entry:
```python
{"name": "Ahmia", "url": "http://<onion>/search/?q={query}"}
```

| # | Engine | Type | URL Template |
|---|--------|------|-------------|
| 1 | Ahmia | .onion | `http://juhanurmihxlp...onion/search/?q={query}` |
| 2 | OnionLand | .onion | `http://3bbad7fa...onion/search?q={query}` |
| 3 | Amnesia | .onion | `http://amnesia7u...onion/search?query={query}` |
| 4 | Torland | .onion | `http://torlbmqwt...onion/index.php?a=search&q={query}` |
| 5 | Excavator | .onion | `http://2fd6cemt...onion/search?query={query}` |
| 6 | Onionway | .onion | `http://oniwayzz...onion/search.php?s={query}` |
| 7 | Tor66 | .onion | `http://tor66seweb...onion/search?q={query}` |
| 8 | OSS | .onion | `http://3fzh7yuup...onion/oss/index.php?search={query}` |
| 9 | Torgol | .onion | `http://torgolnpe...onion/?q={query}` |
| 10 | TheDeepSearches | .onion | `http://searchgf7...onion/search?q={query}` |
| 11 | DuckDuckGo-Tor | .onion | `https://duckduckgogg42xj...onion/?q={query}&ia=web` |
| 12 | Ahmia-clearnet | clearnet | `https://ahmia.fi/search/?q={query}` |

---

### `search(query, engines, max_results, max_workers, mode) -> list[dict]`

Search dark web engines simultaneously through Tor. Results are deduplicated by URL and filtered through the content safety blacklist.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | — | Search keywords (≤5 words recommended for best results on dark web indexes). |
| `engines` | `list[str]` or `None` | `None` | Specific engine names to query. `None` queries all 12. |
| `max_results` | `int` | `20` | Maximum unique results to return (deduplicated by URL). |
| `max_workers` | `int` | `8` | Number of parallel search threads. |
| `mode` | `str` or `None` | `None` | Analysis mode for auto-selecting engines when `engines` is not specified. |

**Returns:**
```json
[
  {
    "title": "Result Title",
    "url": "http://some.onion/page",
    "engine": "Ahmia"
  }
]
```

Each result has:
- `title` (`str`): Link anchor text from the search engine results page.
- `url` (`str`): Extracted `.onion` or clearnet URL (redirect wrappers decoded).
- `engine` (`str`): Name of the search engine that returned this result.

**Note:** Results are **not** confidence-scored by `search()` — that is handled by `osint.score_results()`.

**Example:**
```python
from scripts.engines import search
import json
r = search("example query", max_results=10)
print(json.dumps(r, indent=2))
```

---

### `check_engines(max_workers) -> list[dict]`

Ping all 12 search engines through Tor and return status with latency. Each engine is sent a `GET` request with query "test"; 200 → "up", anything else → "down".

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_workers` | `int` | `8` | Parallel health-check threads. |

**Returns:**
```json
[
  {
    "name": "Ahmia",
    "status": "up",
    "latency_ms": 3420,
    "error": null
  },
  {
    "name": "OnionLand",
    "status": "down",
    "latency_ms": null,
    "error": "HTTP 503"
  }
]
```

Ordered by original engine list. Engines that failed to return a result get `status: "down"` and `error: "no result"`.

**Example:**
```python
from scripts.engines import check_engines
import json
results = check_engines()
alive = [r for r in results if r["status"] == "up"]
dead = [r for r in results if r["status"] != "up"]
print(f"{len(alive)}/{len(results)} alive")
for r in dead:
    print(f"  ✗ {r['name']}: {r['error']}")
```

---

### `mode_engines(mode) -> list[str] or None`

Return recommended engine names for an analysis mode. Returns `None` for modes that should use all available engines.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `mode` | `str` | Analysis mode key: `threat_intel`, `ransomware`, `personal_identity`, `corporate`. |

**Returns:**
- `list[str]` — Engine names for the mode (e.g., `["Ahmia", "Tor66", "Excavator", "Ahmia-clearnet"]`).
- `None` — For `threat_intel` (use all engines).

**Engine routing table:**

| Mode | Preferred Engines |
|------|------------------|
| `threat_intel` | All 12 engines |
| `ransomware` | Ahmia, Tor66, Excavator, Ahmia-clearnet |
| `personal_identity` | Ahmia, OnionLand, Tor66, DuckDuckGo-Tor, Ahmia-clearnet |
| `corporate` | Ahmia, Excavator, Tor66, TheDeepSearches, Ahmia-clearnet |

**Example:**
```python
from scripts.engines import mode_engines
print(mode_engines("ransomware"))
# ["Ahmia", "Tor66", "Excavator", "Ahmia-clearnet"]
```

---

### `mode_seeds(mode) -> list[str]`

Return known seed `.onion` URLs for a mode (e.g., ransomware leak-site blogs). Used by `osint.search_darkweb()` to add verified starting points to results.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `mode` | `str` | Analysis mode key. |

**Returns:**
`list[str]` — Seed `.onion` URLs. Empty list for modes without seeds.

**Known ransomware seeds:**
- ALPHV ransomware blog: `http://alphvmmm27o3...onion`
- LockBit ransomware blog: `http://lockbit7ouvrs...onion`

**Example:**
```python
from scripts.engines import mode_seeds
print(mode_seeds("ransomware"))
# ["http://alphvmmm27o3...onion", "http://lockbit7ouvrs...onion"]
```

---

### `list_modes() -> dict`

Return all available analysis modes with their engine configuration.

**Returns:**
```json
{
  "threat_intel": {"engines": ["all"], "seeds": 0},
  "ransomware": {"engines": ["Ahmia", "Tor66", "Excavator", "Ahmia-clearnet"], "seeds": 2},
  "personal_identity": {"engines": ["Ahmia", "OnionLand", "Tor66", "DuckDuckGo-Tor", "Ahmia-clearnet"], "seeds": 0},
  "corporate": {"engines": ["Ahmia", "Excavator", "Tor66", "TheDeepSearches", "Ahmia-clearnet"], "seeds": 0}
}
```

**Example:**
```python
from scripts.engines import list_modes
import json
print(json.dumps(list_modes(), indent=2))
```

---

## osint.py — Orchestrator Intelligence Tools

High-level tools for the orchestrator (Claude/LLM) to conduct dark web OSINT. This is the primary interface layer — every function here is designed to be called via bash from the orchestrator. All strategic decisions (what to search, which results to pursue, how to interpret findings) are made by the orchestrator.

**File:** `scripts/osint.py`
**Version:** 1.0.0 (matches torcore)

---

### `search_darkweb(query, engines, max_results, mode) -> dict`

Search the dark web and return scored, deduplicated results with metadata. This is the primary endpoint for orchestrator-driven investigations.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | — | Search keywords. Natural language OK but short focused queries (≤5 words) work best on dark web indexes. |
| `engines` | `list[str]` or `None` | `None` | Specific engine names. `None` uses mode-based auto-selection. |
| `max_results` | `int` | `20` | Maximum results to return. |
| `mode` | `str` or `None` | `None` | Analysis mode for auto engine/seed selection. Defaults to `threat_intel`. |

**Returns:**

```json
{
  "query": "example query",
  "mode": "threat_intel",
  "engines_used": ["Ahmia", "Tor66", "Excavator", "Ahmia-clearnet", "seed"],
  "total_raw": 42,
  "results": [
    {
      "title": "Result Title",
      "url": "http://some.onion/page",
      "engine": "Ahmia",
      "confidence": 0.8734
    }
  ],
  "search_time_s": 12.45
}
```

- `results` entries: `{"title", "url", "engine", "confidence"}` — scored by BM25-lite relevance to the query.
- Mode seeds: If a mode has known seed URLs (e.g., ransomware blogs), they are HEAD-checked and appended as bonus results with `engine: "seed"` and `confidence: 0.85`.
- Search time: Wall-clock seconds for the full search + scoring + seed check.

**Example:**
```python
from scripts.osint import search_darkweb
import json
r = search_darkweb("leaked credentials example.com", max_results=20, mode="corporate")
print(json.dumps(r, indent=2))
```

---

### `batch_scrape(urls, max_workers) -> dict`

Fetch multiple URLs concurrently through Tor. Capped at 5 parallel workers to avoid overloading Tor circuits.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `urls` | `list[str]` | — | List of URLs to fetch. |
| `max_workers` | `int` | `5` | Maximum parallel threads (capped at `min(max_workers, len(urls))`). |

**Returns:**
```json
{
  "http://site1.onion/page": {
    "url": "http://site1.onion/page",
    "is_onion": true,
    "status": 200,
    "title": "Page Title",
    "text": "Page content...",
    "links": [{"text": "...", "href": "..."}],
    "truncated": false,
    "error": null
  },
  "http://site2.onion/other": {
    "title": null,
    "text": "",
    "error": "Tor circuit timed out — hidden service may be offline"
  }
}
```

Each value is the same schema as `torcore.fetch()`. Failed URLs get `{"title": None, "text": "", "error": str}`.

**Example:**
```python
from scripts.osint import batch_scrape
import json
urls = ["http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"]
r = batch_scrape(urls)
print(json.dumps(r, indent=2))
```

---

### `score_results(query, results, page_texts) -> list[dict]`

Score results by keyword overlap with query using BM25-lite. Pure stdlib — no external dependencies. Adds a `"confidence"` score (0.0–1.0) to each result dict.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | — | The search query. If a list is passed, it's joined with spaces. |
| `results` | `list[dict]` | — | Result dicts with at least `"title"` and `"url"` keys. |
| `page_texts` | `dict[str, str]` or `None` | `None` | Optional `{url: text}` mapping for deeper content-level scoring. |

**Returns:**
Same list of dicts with `"confidence"` (`float`, 0.0–1.0) added, sorted best-first. Results with no query term overlap get a baseline of `0.05`. Results with empty query terms get `0.5`.

**Algorithm:**
- BM25 variant with `k1=1.5`, `b=0.75`, `avgdl=12.0`
- Term frequency in title/url/snippet/page_text fields
- Stopwords removed

**Example:**
```python
from scripts.osint import score_results
results = [{"title": "Example Corp Data Leak", "url": "http://onion/leak"}]
scored = score_results("corp data leak", results)
print(scored[0]["confidence"])  # e.g., 0.7234
```

---

### `extract_entities(text) -> dict`

Extract structured intelligence entities from raw text using regex patterns. Finds emails, `.onion` URLs, cryptocurrency addresses, PGP keys, phone numbers, IPs, and domains.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `text` | `str` | Raw text from a fetched page or combined content. |

**Returns:**
```json
{
  "emails": ["user@example.com"],
  "onion_links": ["http://some.onion/page"],
  "btc_addresses": ["bc1q...xyz"],
  "xmr_addresses": ["4..."),
  "eth_addresses": ["0x..."],
  "pgp_keys": true,
  "phones": ["+1 555 123 4567"],
  "ips": ["192.168.1.1"],
  "domains": ["example.com"]
}
```

- `emails` (`list[str]`): Sorted unique email addresses.
- `onion_links` (`list[str]`): Sorted unique `.onion` URLs with paths.
- `btc_addresses` (`list[str]`): Bitcoin addresses (legacy `1...`, SegWit `bc1...`).
- `xmr_addresses` (`list[str]`): Monero addresses (95-char `4...` pattern).
- `eth_addresses` (`list[str]`): Ethereum addresses (`0x` + 40 hex chars).
- `pgp_keys` (`bool`): `True` if PGP block markers detected (`BEGIN PGP`, `END PGP`).
- `phones` (`list[str]`): Phone numbers with country codes.
- `ips` (`list[str]`): IPv4 addresses.
- `domains` (`list[str]`): Domain names extracted from URLs and text.

**Example:**
```python
from scripts.osint import extract_entities
import json
text = "Contact: user@example.com, BTC: bc1qxyz, PGP: -----BEGIN PGP-----"
r = extract_entities(text)
print(json.dumps(r, indent=2))
```

---

### `extract_keywords(text, top_n) -> list[str]`

Extract top keywords using TF-IDF-like scoring. Pure stdlib — no external dependencies.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | — | Plain text to analyze. |
| `top_n` | `int` | `25` | Number of top keywords to return. |

**Returns:**
`list[str]` — Ordered list of keywords, highest-scoring first. Stopwords removed, minimum 3-character tokens.

**Algorithm:**
- Term frequency (`cnt / total`)
- IDF-like damping: `1 / log(cnt + 2)`
- Final score: `(cnt / total) * (1 / log(cnt + 2))`

**Example:**
```python
from scripts.osint import extract_keywords
text = "Data breach at Example Corp exposed 1 million user records including emails and passwords"
kw = extract_keywords(text, top_n=5)
print(kw)  # ["data", "breach", "example", "corp", "exposed"]
```

---

### `content_fingerprint(text) -> str`

Generate a content fingerprint for near-duplicate detection. Normalizes whitespace and removes common boilerplate (copyright, terms, privacy notices) before MD5 hashing the first 4096 characters.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `text` | `str` | Text to fingerprint. |

**Returns:**
`str` — MD5 hex digest of the normalized, deduplicated content prefix.

**Example:**
```python
from scripts.osint import content_fingerprint
fp = content_fingerprint("Some page content")
print(fp)  # "d41d8cd98f00b204e9800998ecf8427e"
```

---

### `format_output(results, fmt, query, report_text) -> str`

Format search/scrape results for export. Supports five output formats.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `results` | `list[dict]` | — | List of `{"title", "url", "engine", "confidence"}` dicts. |
| `fmt` | `str` | `"json"` | Output format: `json`, `csv`, `stix`, `misp`, `text`. |
| `query` | `str` | `""` | Investigation query (for report headers in STIX/MISP). |
| `report_text` | `str` | `""` | Full OSINT report text (embedded in STIX notes and MISP comments). |

**Returns:**
`str` — Formatted string ready for file output.

**Format descriptions:**

| Format | Description |
|--------|-------------|
| `json` | `json.dumps(results, indent=2, default=str)` — simple JSON array. |
| `csv` | CSV with header row: `title, url, engine, confidence`. |
| `stix` | STIX 2.1 bundle with identity, report, URL observables, relationships, and optional note containing `report_text`. |
| `misp` | MISP event JSON with URL and domain attributes, TLP:AMBER tagging. |
| `text` | Human-readable numbered list with engine and confidence annotations. |

**Example:**
```python
from scripts.osint import format_output
results = [{"title": "Test", "url": "http://onion", "engine": "Ahmia", "confidence": 0.85}]

# CSV output
print(format_output(results, "csv"))

# STIX 2.1 bundle
print(format_output(results, "stix", query="investigation"))

# MISP event
print(format_output(results, "misp", query="investigation", report_text="Full findings..."))

# Human-readable text
print(format_output(results, "text"))
```
