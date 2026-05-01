# Safety & Legal Guidelines

## Content Safety Blacklist

OpenTor includes an **automatic content safety blacklist** in `scripts/torcore.py` that blocks illegal content (CSAM, snuff, violent sexual material involving minors) from being returned in search results, fetched pages, or scraped content. This filter **is automatic and cannot be disabled** — it runs at the transport layer on every `fetch()` call and every `search()` result.

### How It Works

The filter uses three layers of detection, all case-insensitive:

**1. Keyword blacklist** — A `frozenset` of known illegal-content terms:
```python
_CONTENT_BLACKLIST = frozenset({
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
```
Any result containing a blacklisted phrase is silently dropped from search output. `fetch()` returns a blocked error dict: `{"title": "[blocked]", "text": "", "error": "Content matches safety blacklist"}`.

**2. Token-pair matching** — Catches evasive titles that bypass exact phrase matching by spreading words across separators:
```python
_TOKEN_PAIRS = (
    ("child", "rape"), ("child", "torture"), ("child", "minor"),
    ("minor", "rape"), ("minor", "torture"),
    ("kids", "rape"), ("kids", "sex"), ("kids", "porn"), ("kids", "child"),
    ("baby", "rape"), ("infant", "rape"), ("teen", "rape"),
    ("snuff", "live"),
)
```
If both words in any token pair appear anywhere in the text, the content is blocked.

**3. Context-sensitive "rape" detection** — The standalone word `"rape"` (word-boundary match) is only blocked when it appears alongside context words (`video`, `film`, `porn`, `child`, `minor`, `teen`, `kids`, `baby`, `infant`, `market`, `onion`, etc.). This allows legitimate criminology, news, and threat intelligence mentions while blocking exploitation content.

### Where It Applies

| Component | Behavior |
|-----------|----------|
| `torcore.fetch()` | Returns blocked error dict if URL, title, or body text matches blacklist |
| `engines.search()` | Matching results are excluded from the output list (silently dropped) |
| `osint.search_darkweb()` | Inherits engine-level filtering + content safety on results |
| `osint.batch_scrape()` | Each URL's fetch result is independently filtered |
| `osint.score_results()` | Results already filtered upstream; no additional filter |

### Scope

The blacklist covers:
- Child sexual abuse material (CSAM) terms
- Snuff / red room / hurtcore content
- Violent sexual content involving minors
- Evasive token-pair combinations designed to bypass simple filters

---

## Responsible Use Guidelines

OpenTor is built for **legitimate OSINT, threat intelligence, and security research purposes only**.

### Do's
- Use for authorized penetration testing, red teaming, and security research
- Investigate threat actors, ransomware groups, and cybercrime infrastructure
- Monitor for data breaches involving your organization or authorized subjects
- Produce threat intelligence reports for defensive cybersecurity purposes
- Export findings to STIX 2.1 or MISP for sharing with trusted security teams

### Don'ts
- Do not use for accessing, viewing, or distributing illegal content
- Do not use to stalk, harass, or doxx individuals
- Do not use to access credentials or data without authorization
- Do not use in jurisdictions where Tor usage or dark web access is restricted without legal basis
- Do not bypass or attempt to disable the content safety blacklist

---

## The Orchestrator-Conductor Model — Additional Responsibility

OpenTor uses an **orchestrator-conductor architecture**:

- **Orchestrator (AI/LLM)** — Drives the investigation: decides what to search, which results to pursue, how to interpret findings, and what to report. All intelligence and analysis flows through the orchestrator.
- **Conductor (Python modules)** — Pure mechanical operations: routing traffic through Tor, querying search engines, scraping pages, extracting entities, formatting output.

Because the orchestrator (AI) is responsible for all analysis decisions, it carries additional responsibility:

1. **Content awareness** — The orchestrator should be aware that scraped content may contain distressing material. The safety blacklist provides first-line defense, but the orchestrator should also exercise judgment.
2. **Privacy protection** — When extracting entities (emails, phone numbers, addresses), the orchestrator must handle personal data with appropriate discretion. Avoid displaying full PII in reports unless necessary for the investigation.
3. **Context preservation** — The orchestrator should analyze scraped content in context, not in isolation. A keyword match for "exploit" could refer to a software exploit (legitimate threat intel) or something else entirely.
4. **Source verification** — Dark web content is often intentionally misleading. The orchestrator should cross-reference findings and avoid treating any single `.onion` source as authoritative.

---

## Tor Operational Security ≠ Legal Protection

- Tor provides **operational security** (opsec) by anonymizing your traffic through a distributed network of relays. Your ISP can see you're using Tor but cannot see your destination traffic.
- Tor does **not** provide **legal protection**. Activities that are illegal on the clearnet are equally illegal on Tor.
- The Tor exit node IP visible to remote servers is **not your IP** — but law enforcement can still identify Tor users through timing analysis, endpoint monitoring, or compromised nodes.
- Using Tor does not grant immunity from:
  - Terms of service violations of websites you access
  - Laws regarding automated scraping or data access
  - Regulations on possession of certain types of data (credentials, PII)

---

## Disclaimer

OpenTor is provided **for educational and authorized security research purposes only**. Users are responsible for complying with all applicable local, national, and international laws.

**The developers assume no liability for:**
- Misuse of the tool
- Violation of terms of service of any search engine or website
- Unauthorized access to protected systems
- Any legal consequences arising from use of this tool
- Content accessed through Tor that triggers safety filters

If you are unsure about the legality of using this tool in your jurisdiction, consult with legal counsel before proceeding.

---

## License

OpenTor is released under the **MIT License** (see `LICENSE` file).

Copyright (c) 2026 OpenTor Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
