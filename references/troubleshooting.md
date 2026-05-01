# Troubleshooting

Common issues, symptoms, and fixes for OpenTor.

---

## Tor Not Active

**Symptom:** `check_tor()` returns `{"tor_active": false, ...}`

**Fix:** Install and start Tor:

```bash
# Debian/Ubuntu
sudo apt install tor
sudo systemctl start tor

# macOS
brew install tor
brew services start tor

# Verify SOCKS port is listening
python3 -c "
from scripts.torcore import check_tor
import json; print(json.dumps(check_tor(), indent=2))
"
```

If Tor is running but `check_tor()` still fails, verify the SOCKS port:
```bash
ss -tlnp | grep 9050    # Linux
lsof -i :9050            # macOS/Linux
```

The default SOCKS port is `9050`. If you use a different port, set it in `.env`:
```
TOR_SOCKS_PORT=9150
```

---

## `renew_identity()` Fails

**Symptom:** `renew_identity()` returns `{"success": false, "error": "..."}`

**Fix:** Tor's ControlPort (default 9051) must be enabled for identity renewal.

**Step 1 — Enable ControlPort in torrc:**
```bash
echo "ControlPort 9051" | sudo tee -a /etc/tor/torrc
echo "CookieAuthentication 1" | sudo tee -a /etc/tor/torrc
sudo systemctl restart tor
```

**Step 2 — Or use the setup wizard which configures this automatically:**
```bash
python3 scripts/setup.py
```

**Step 3 — Verify ControlPort is reachable:**
```bash
python3 -c "
import socket
try:
    socket.create_connection(('127.0.0.1', 9051), timeout=2).close()
    print('ControlPort 9051 is reachable')
except OSError as e:
    print(f'ControlPort not reachable: {e}')
"
```

**Step 4 — If using password authentication, set in `.env`:**
```
TOR_CONTROL_PASSWORD=your_password_here
TOR_DATA_DIR=/var/lib/tor
```

**Dependency:** `pip install stem` is required for `renew_identity()`.

---

## `fetch()` Returns Status 0

**Symptom:** `fetch(url)` returns `{"status": 0, "error": "Tor circuit timed out — hidden service may be offline"}`

**Explanation:** Status 0 means the request never received an HTTP response. `.onion` hidden services are often transient — they go offline frequently and unexpectedly.

**Fixes:**

1. Check Tor is healthy first:
   ```bash
   python3 -c "from scripts.torcore import check_tor; import json; print(json.dumps(check_tor(), indent=2))"
   ```

2. Rotate the Tor circuit (sometimes a fresh circuit can reach an otherwise unreachable service):
   ```bash
   python3 -c "from scripts.torcore import renew_identity; import json; print(json.dumps(renew_identity(), indent=2))"
   ```

3. Try a different URL from your search results — the same content may be mirrored elsewhere.

4. The service may simply be down permanently. `.onion` sites have typical lifespans measured in months, not years.

---

## `search()` Returns 0 Results

**Symptom:** `search()` or `search_darkweb()` returns an empty results list.

**Fix:** Check which search engines are responsive first:

```bash
python3 -c "
from scripts.engines import check_engines
results = check_engines()
alive = [r for r in results if r['status'] == 'up']
dead = [r for r in results if r['status'] != 'up']
print(f'{len(alive)}/{len(results)} engines alive')
for r in dead:
    print(f'  ✗ {r[\"name\"]}: {r.get(\"error\", \"down\")[:60]}')
"
```

Common scenarios:

| Scenario | Fix |
|----------|-----|
| All engines down | Renew Tor circuit, wait 10s, retry |
| Only 1-2 engines up | Target specific engines: `search_darkweb(query, engines=["Ahmia", "Ahmia-clearnet"])` |
| `mode_engines()` returns None | `threat_intel` mode uses all engines — check Tor health first |
| All engines up but 0 results | Your query may be too specific; try shorter keywords (≤5 words) |

**Minimum reliable fallback engines:** `Ahmia`, `Ahmia-clearnet`, `DuckDuckGo-Tor`

```bash
python3 -c "
from scripts.osint import search_darkweb
import json
r = search_darkweb('test query', engines=['Ahmia', 'Ahmia-clearnet'], max_results=5)
print(f'Results: {len(r[\"results\"])}')
"
```

---

## Missing Dependencies

**Symptom:** `ModuleNotFoundError` or `ImportError` when importing OpenTor modules.

**Fix:** Install all required packages:

```bash
pip install requests[socks] beautifulsoup4 python-dotenv stem
```

**Package breakdown:**

| Package | Required For | Import Name |
|---------|-------------|-------------|
| `requests[socks]` | SOCKS5 proxy support through Tor | `requests` |
| `beautifulsoup4` | HTML parsing in `fetch()` and `search()` | `bs4` |
| `python-dotenv` | `.env` file loading for configuration | `dotenv` |
| `stem` | Tor ControlPort auth for `renew_identity()` | `stem` |

**Verify installation:**
```bash
python3 -c "
import requests, bs4, dotenv, stem
print('All required packages are installed')
"
```

---

## Module Import Errors (`sys.path`)

**Symptom:** `ModuleNotFoundError: No module named 'scripts'` or `No module named 'torcore'`

**Fix:** All OpenTor commands must be run from the **project root directory** (the directory containing `scripts/`):

```bash
# Correct — from project root
cd /path/to/OpenTor
python3 -c "from scripts import torcore; print(torcore.check_tor())"

# Wrong — scripts dir as working directory
cd /path/to/OpenTor/scripts
python3 -c "from scripts import torcore"   # FAILS
python3 -c "import torcore"                # FAILS (unless PYTHONPATH set)
```

If you must run from a different directory, add the project root to `sys.path`:
```bash
python3 -c "
import sys
sys.path.insert(0, '/path/to/OpenTor')
from scripts import torcore
print(torcore.check_tor())
"
```

For persistent configuration, set the `PYTHONPATH` environment variable:
```bash
export PYTHONPATH=/path/to/OpenTor:$PYTHONPATH
python3 -c "from scripts import torcore"
```

---

## Database Path (~/.opentor/)

**Symptom:** File I/O errors related to `~/.opentor/` directory.

**Explanation:** Some OpenTor components store cached data and configuration in `~/.opentor/`. This directory is created automatically on first use.

**Fixes:**

Ensure the directory exists and is writable:
```bash
ls -la ~/.opentor/
# If missing, create it:
mkdir -p ~/.opentor
chmod 700 ~/.opentor
```

If the directory becomes corrupted:
```bash
# Safely remove cache contents (will be recreated on next use)
rm -rf ~/.opentor/cache
```

---

## Engine Health Check Is Slow

**Symptom:** `check_engines()` takes 60+ seconds.

**Explanation:** All 12 engines are contacted in parallel through Tor, but each request must traverse the Tor network. Typical latency per request is 2-8 seconds. With 8 parallel workers, the full check takes approximately 20-60 seconds depending on Tor circuit speed and engine availability.

**Tips:**
- This is normal behavior — Tor is inherently slow
- Run `check_engines()` once and cache the results in your investigation session
- Focus on the subset of alive engines for subsequent searches

---

## Content Safety Filter Blocking Legitimate Content

**Symptom:** A legitimate OSINT or news page is blocked by the safety filter.

**Explanation:** The blacklist is intentionally conservative. Some false positives are possible when the filter encounters words like "rape" in a legitimate threat intelligence context (e.g., "rape kits sold on dark web" or "RapidL7 ransomware").

**What to do:**
- The filter **cannot be disabled** by design
- Restructure your query to avoid triggering terms (e.g., use "sexual assault" instead of "rape" if that fits the investigation)
- Fetch pages individually rather than through search to see which specific term triggered the block
- The filter only blocks the text content — the URL and status code are still returned in the error dict

---

## General Debugging

Enable verbose logging to see what's happening:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
from scripts import torcore, engines, osint
```

Or set the `PYTHONLOGLEVEL` environment variable:
```bash
PYTHONLOGLEVEL=DEBUG python3 -c "
from scripts.osint import search_darkweb
import json
r = search_darkweb('test', max_results=3)
print(json.dumps(r, indent=2))
"
```

---

## Quick Reference — Command Cheat Sheet

| Task | Command |
|------|---------|
| Check Tor | `python3 -c "from scripts.torcore import check_tor; import json; print(json.dumps(check_tor(), indent=2))"` |
| Renew identity | `python3 -c "from scripts.torcore import renew_identity; import json; print(json.dumps(renew_identity(), indent=2))"` |
| Fetch URL | `python3 -c "from scripts.torcore import fetch; import json; print(json.dumps(fetch('URL'), indent=2))"` |
| Check engines | `python3 -c "from scripts.engines import check_engines; import json; print(json.dumps(check_engines(), indent=2))"` |
| Search dark web | `python3 -c "from scripts.osint import search_darkweb; import json; print(json.dumps(search_darkweb('query'), indent=2))"` |
| Batch scrape | `python3 -c "from scripts.osint import batch_scrape; import json; print(json.dumps(batch_scrape(['URL1', 'URL2']), indent=2))"` |
| Extract entities | `python3 -c "from scripts.osint import extract_entities; import json; print(json.dumps(extract_entities('TEXT'), indent=2))"` |
| Setup wizard | `python3 scripts/setup.py` |
| Install deps | `pip install requests[socks] beautifulsoup4 python-dotenv stem` |
