#!/usr/bin/env python3
"""Daily primary-source checks and discovery for the Competition Atlas.

Verification is conservative: a reachable homepage never means a contest is open.
Search-engine results are stored as unverified leads; only manually reviewed official
rules can mark an entry as verified. No external API keys or pip dependencies.
"""
import concurrent.futures
import datetime as dt
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TODAY = dt.datetime.now(dt.timezone.utc).date().isoformat()
HEADERS = {"User-Agent": "CompetitionAtlasResearch/1.0 (source verification; Github Actions; contact via repository issues)", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
QUERIES = [
    '"semiconductor" "student" "competition" "2027"',
    '"FPGA" "design contest" "2027"',
    '"integrated circuit" "student design" competition 2027',
    '"nanoelectronics" "competition" students 2027',
    '"IEEE" "sensor" "student" contest 2027',
    '"VLSI" "CAD" design contest 2027',
    '"photonic" "student" challenge 2027',
    '"chipathon" "open source" design contest',
]
TERMS = re.compile(r"\b(chip|fpga|asic|vlsi|nano|semiconductor|ic design|circuit|sensor|photon|rf\b|mems|eda|embedded|electronic|hardware|packaging)\b", re.I)

def fetch(url, limit=500_000):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=12) as response:
        final = response.geturl()
        body = response.read(limit)
        typ = response.headers.get("Content-Type", "")
        return final, body, response.status, typ

def verify(pair):
    url, _ = pair
    try:
        final, body, status, typ = fetch(url)
        # 403 / bot-protection can indicate inaccessible content, not a dead contest.
        txt = body.decode("utf-8", "replace")
        blocked = any(t in txt[:4000].lower() for t in ("please wait while your request is being verified", "checking your browser", "enable javascript and cookies", "captcha"))
        return url, {"status": "blocked" if blocked else "reachable", "http": status,
                     "final_url": final, "sha256": hashlib.sha256(body).hexdigest()[:16],
                     "checked_utc": TODAY, "content_type": typ[:70]}
    except Exception as e:
        return url, {"status": "unreachable", "error": str(e)[:150], "checked_utc": TODAY}

def discover(query):
    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    # Google News RSS is an independent candidate generator; never evidence of eligibility.
    rss = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en"
    try:
        _, body, _, _ = fetch(rss, 600_000)
        root = ET.fromstring(body)
        out = []
        for item in root.findall(".//item")[:15]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title and link.startswith("https://") and TERMS.search(title):
                out.append({"title": title[:240], "lead_url": link, "query": query,
                            "discovered": TODAY, "review": "unverified_news_lead"})
        return out
    except Exception as e:
        print("Discovery unavailable:", query, str(e)[:90])
        return []

def main():
    path = DATA / "competitions.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    items = content["records"]
    urls = sorted({e["source"] for e in items if e.get("source", "").startswith("https://")})
    checks = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for url, result in pool.map(verify, [(u, None) for u in urls]):
            checks[url] = result
    closed_now = 0
    for e in items:
        if e.get("deadline") and e["deadline"] < TODAY and e.get("status") in ("open", "upcoming"):
            e["status"] = "closed"
            closed_now += 1
        # Do not change reviewed, deadline, eligibility or call-open status on HTTP checks.
    DATA.mkdir(exist_ok=True)
    (DATA / "source-health.json").write_text(json.dumps({
        "checked_utc": TODAY, "sources_total": len(urls),
        "reachable": sum(x["status"] == "reachable" for x in checks.values()),
        "blocked": sum(x["status"] == "blocked" for x in checks.values()),
        "sources": checks,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    existing_file = DATA / "research-leads.json"
    existing = json.loads(existing_file.read_text(encoding="utf-8")) if existing_file.exists() else []
    index = {re.sub(r"[^a-z0-9]", "", x["title"].lower()): x for x in existing}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for found in pool.map(discover, QUERIES):
            for lead in found:
                key = re.sub(r"[^a-z0-9]", "", lead["title"].lower())
                index.setdefault(key, lead)
    fresh = sorted(index.values(), key=lambda x: x.get("discovered", ""), reverse=True)[:400]
    existing_file.write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    content["meta"]["last_automatic_check"] = TODAY
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DATA / "research-state.json").write_text(json.dumps({
        "run_date_utc": TODAY,
        "records": len(items),
        "official_sources_checked": len(urls),
        "sources_reachable": sum(c["status"] == "reachable" for c in checks.values()),
        "sources_blocked": sum(c["status"] == "blocked" for c in checks.values()),
        "deadlines_expired_this_run": closed_now,
        "unverified_discovery_leads": len(fresh),
        "note": "Automated HTTP and RSS checks do NOT verify contest eligibility/open calls. Review official rules manually.",
    }, indent=2) + "\n")
    print("Research cycle", TODAY, "sources", len(urls), "leads", len(fresh), "expired", closed_now)

if __name__ == "__main__":
    main()
