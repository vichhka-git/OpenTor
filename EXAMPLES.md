# OpenTor Usage Examples

Real usage examples using `python3 -c` import patterns. Run these from the OpenTor project root directory.

All examples assume:
- Tor is running locally (SOCKS port 9050)
- Dependencies installed (`pip install requests[socks] beautifulsoup4 python-dotenv stem`)
- Python 3.9+

---

## Example 1: Quick Dark Web Search

Check Tor connectivity, then perform a dark web search for a query.

```bash
python3 -c "
import sys, json
from scripts import torcore, osint

# Step 1: Verify Tor
status = torcore.check_tor()
print('Tor active:', status['tor_active'])
print('Exit IP:', status['exit_ip'])
print()

# Step 2: Search dark web
results = osint.search_darkweb('data breach example', max_results=5, mode='threat_intel')
print(json.dumps(results, indent=2))
"
```

**Expected output (abbreviated):**
```json
Tor active: True
Exit IP: 185.220.101.x

{
  "query": "data breach example",
  "mode": "threat_intel",
  "engines_used": ["Ahmia", "Tor66", "Ahmia-clearnet", ...],
  "total_raw": 18,
  "results": [
    {
      "title": "Example Corp Data Leak",
      "url": "http://some.onion/leak",
      "engine": "Ahmia",
      "confidence": 0.8734
    }
  ],
  "search_time_s": 14.32
}
```

---

## Example 2: Fetch a .onion Page with Entity Extraction

Fetch a dark web page, extract intelligence entities (emails, crypto addresses, domains), and format the output.

```bash
python3 -c "
import sys, json
from scripts import torcore, osint

# Fetch a page
url = 'http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion'
page = torcore.fetch(url, max_chars=3000)

if page['error']:
    print('Error:', page['error'])
else:
    print('Title:', page['title'])
    print('Status:', page['status'])
    print('Text (first 500 chars):', page['text'][:500])
    print('Links found:', len(page['links']))
    print()

    # Extract entities from the combined text
    entities = osint.extract_entities(page['text'] + ' ' + page['title'] if page['title'] else page['text'])
    print('Entities found:')
    for etype, values in entities.items():
        if values:
            print(f'  {etype}: {values[:3]}')  # show first 3
"
```

**Expected output (abbreviated):**
```
Title: Ahmia — Search Tor Hidden Services
Status: 200
Text (first 500 chars): Ahmia is a search engine for Tor hidden services...
Links found: 12

Entities found:
  onion_links: ['http://ahmia.onion/']
  domains: ['ahmia.fi']
```

---

## Example 3: Ransomware Investigation

Use `ransomware` mode to search for a ransomware group, batch scrape the results, and export a STIX 2.1 bundle.

```bash
python3 -c "
import sys, json
from scripts import osint, torcore

# Step 1: Check Tor
if not torcore.check_tor()['tor_active']:
    print('Tor not active')
    sys.exit(1)

# Step 2: Search with ransomware mode
print('Searching dark web for ransomware intelligence...')
results = osint.search_darkweb('lockbit ransomware victims', max_results=10, mode='ransomware')
print(f'Got {results[\"total_raw\"]} raw results, {len(results[\"results\"])} after scoring')
print('Engines used:', results['engines_used'])

# Step 3: Batch scrape top results
top_urls = [r['url'] for r in results['results'][:3]]
if top_urls:
    print(f'\\nScraping {len(top_urls)} pages...')
    scraped = osint.batch_scrape(top_urls)
    for url, data in scraped.items():
        status = 'OK' if not data.get('error') else f'FAIL: {data[\"error\"][:50]}'
        print(f'  {url}: {status}')

# Step 4: Keyword analysis on scraped text
all_text = ' '.join(d.get('text', '') for d in scraped.values())
kw = osint.extract_keywords(all_text, top_n=10)
print(f'\\nTop keywords: {kw}')

# Step 5: Export to STIX
stix_output = osint.format_output(results['results'], fmt='stix', query='lockbit ransomware')
with open('/tmp/ransomware_intel.json', 'w') as f:
    f.write(stix_output)
print('\\nSTIX bundle written to /tmp/ransomware_intel.json')
print('Bundle size:', len(stix_output), 'chars')
"
```

---

## Example 4: Corporate Leak Check

Search for leaked corporate credentials, scrape results, extract keywords, and generate a human-readable report.

```bash
python3 -c "
import sys, json
from scripts import osint, torcore

# Quick Tor check
status = torcore.check_tor()
if not status['tor_active']:
    print('Tor not active:', status['error'])
    sys.exit(1)

# Search with corporate mode
r = osint.search_darkweb('acmecorp credentials leaked', max_results=8, mode='corporate')
print(f'Query: {r[\"query\"]}')
print(f'Mode: {r[\"mode\"]}')
print(f'Results found: {len(r[\"results\"])}')
print()

# Scrape top results
urls = [res['url'] for res in r['results'][:4]]
scraped = osint.batch_scrape(urls)

# Extract keywords from all content
all_text = ' '.join(d.get('text', '') for d in scraped.values() if d.get('text'))
keywords = osint.extract_keywords(all_text, top_n=15)
print('Top keywords:')
print(', '.join(keywords))

# Extract entities
entities = osint.extract_entities(all_text)
print()
print('Emails found:', len(entities['emails']))
print('BTC addresses:', len(entities['btc_addresses']))
print('Domains:', entities['domains'][:5])
"
```

---

## Example 5: Personal Data Exposure

Search for personal identity exposure on the dark web and fetch the top results for analysis.

```bash
python3 -c "
import sys, json
from scripts import osint, torcore

# Check Tor
status = torcore.check_tor()
print(f'Tor: {\"active\" if status[\"tor_active\"] else \"inactive\"} (IP: {status[\"exit_ip\"]})')

# Search with personal_identity mode
results = osint.search_darkweb('john.doe@example.com', max_results=10, mode='personal_identity')
print(f'Results: {len(results[\"results\"])} from {results[\"engines_used\"]}')
print(f'Search completed in {results[\"search_time_s\"]}s')
print()

# Fetch top 3 results
for res in results['results'][:3]:
    page = torcore.fetch(res['url'], max_chars=2000)
    status_icon = '✓' if page['status'] == 200 else '✗'
    print(f'{status_icon} {res[\"url\"][:60]:60s} [{page[\"status\"]}] {page.get(\"title\", \"\")[:40]}')
    if page['text']:
        entities = osint.extract_entities(page['text'])
        if entities['emails'] or entities['phones']:
            print(f'   Emails: {entities[\"emails\"][:2]}')
            print(f'   Phones: {entities[\"phones\"][:2]}')
    print()
"
```

---

## Example 6: Export to STIX 2.1

Demonstrate formatting scraped results as a structured STIX 2.1 bundle with full metadata.

```bash
python3 -c "
import sys, json
from scripts import osint

# Simulate results from a previous search
results = [
    {'title': 'Ransomware group posts leaked data', 'url': 'http://example.onion/leak1', 'engine': 'Ahmia', 'confidence': 0.92},
    {'title': 'Credentials dump for corp.com', 'url': 'http://another.onion/dump', 'engine': 'Tor66', 'confidence': 0.78},
    {'title': 'Dark web marketplace listing', 'url': 'http://market.onion/item', 'engine': 'Excavator', 'confidence': 0.65},
]

# Export as STIX 2.1 bundle
stix = osint.format_output(
    results,
    fmt='stix',
    query='corporate data leak investigation',
    report_text='Investigation conducted via OpenTor. Found 3 relevant pages with credential exposure.'
)

# Parse and display structure
bundle = json.loads(stix)
print(f'STIX bundle ID: {bundle[\"id\"]}')
print(f'Objects in bundle: {len(bundle[\"objects\"])}')
print()
for obj in bundle['objects']:
    obj_type = obj['type']
    name = obj.get('name', obj.get('value', obj.get('id', '')))[:50]
    print(f'  [{obj_type:>14}] {name}')
"
```

**Expected output:**
```
STIX bundle ID: bundle--<uuid>
Objects in bundle: 10

  [      identity] OpenTor v1.0.0
  [        report] OpenTor OSINT: corporate data leak investigation
  [           url] http://example.onion/leak1
  [ relationship] relationship--<uuid>
  [           url] http://another.onion/dump
  [ relationship] relationship--<uuid>
  [           url] http://market.onion/item
  [ relationship] relationship--<uuid>
  [          note] OpenTor OSINT Report: corporate data leak investigation
```

---

## Example 7: Rotating Identity Between Sessions

Demonstrate identity rotation between two separate investigation sessions, with Tor health verification before and after.

```bash
python3 -c "
import sys, json
from scripts import torcore, osint

print('=== Session 1: Initial identity ===')
status1 = torcore.check_tor()
print(f'Exit IP: {status1[\"exit_ip\"]}')
print()

# Perform a search
results1 = osint.search_darkweb('threat actor profile', max_results=3, mode='threat_intel')
print(f'Session 1 results: {len(results1[\"results\"])}')
print()

# Rotate Tor identity
print('=== Rotating Tor identity... ===')
rotate = torcore.renew_identity()
print(f'Renew success: {rotate[\"success\"]}')
print()

# Wait for circuit to stabilize
import time
time.sleep(2)

print('=== Session 2: Fresh identity ===')
status2 = torcore.check_tor()
print(f'Exit IP: {status2[\"exit_ip\"]}')
print(f'Identity changed: {status1[\"exit_ip\"] != status2[\"exit_ip\"]}')
print()

# Perform a different search with the new identity
results2 = osint.search_darkweb('ransomware indicators', max_results=3, mode='ransomware')
print(f'Session 2 results: {len(results2[\"results\"])}')
"
```

**Expected output:**
```
=== Session 1: Initial identity ===
Exit IP: 185.220.101.x

Session 1 results: 7

=== Rotating Tor identity... ===
Renew success: True

=== Session 2: Fresh identity ===
Exit IP: 185.220.101.y
Identity changed: True

Session 2 results: 5
```

---

## Example 8: Engine Health Check

Check all 12 search engines for availability and latency, with a clean alive/dead summary.

```bash
python3 -c "
import sys, json
from scripts import engines

print('Checking all 12 dark web search engines...')
print('(This may take 30-60 seconds due to Tor latency)')
print()

results = engines.check_engines()
alive = [r for r in results if r['status'] == 'up']
dead = [r for r in results if r['status'] != 'up']

print(f'{\"Alive Engines\":_^60}')
print(f'{len(alive)}/{len(results)} engines reachable')
print()
for r in sorted(alive, key=lambda x: x.get('latency_ms') or 9999):
    bar = '#' * max(1, (r.get('latency_ms') or 0) // 1000)
    print(f'  ✓ {r[\"name\"]:<25} {r[\"latency_ms\"]:>5}ms  {bar}')

print()
print(f'{\"Dead Engines\":_^60}')
if dead:
    for r in dead:
        print(f'  ✗ {r[\"name\"]:<25} {r.get(\"error\", \"unknown\")[:50]}')
else:
    print('  (none — all engines reachable)')

print()
print('Alive engine names:')
print(' '.join(r['name'] for r in alive))
"
```

**Expected output (abbreviated):**
```
Checking all 12 dark web search engines...
(This may take 30-60 seconds due to Tor latency)

____________________Alive Engines_____________________
8/12 engines reachable

  ✓ Ahmia                      3240ms  ###
  ✓ Tor66                      4100ms  ####
  ✓ DuckDuckGo-Tor             5200ms  #####
  ✓ Excavator                  6800ms  ######
  ✓ Ahmia-clearnet             1200ms  #
  ✓ OnionLand                  8900ms  ########
  ✓ TheDeepSearches            7500ms  #######
  ✓ Torgol                     6100ms  ######

_____________________Dead Engines_____________________
  ✗ Amnesia        HTTP 503
  ✗ Torland        ConnectionRefusedError
  ✗ Onionway       TimeoutError
  ✗ OSS            HTTP 502

Alive engine names:
Ahmia Tor66 DuckDuckGo-Tor Excavator Ahmia-clearnet OnionLand TheDeepSearches Torgol
```
