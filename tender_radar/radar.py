"""Read a small set of public PPRA notices and publish a conservative IT shortlist."""
from __future__ import annotations
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

BASE = "https://epms.ppra.gov.pk"
LIST_URL = BASE + "/public/tenders/active-tenders"
TZ = ZoneInfo("Asia/Karachi")
ID = re.compile(r"\b(?:TS|FW)\d{8,12}E\b", re.I)
DATE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}(?:\s+(?:0?[1-9]|1[0-2]):\d{2}\s*[AP]M)?\b", re.I)
KEYWORDS = {
    "software development": 9, "online payment": 9, "payment integration": 9,
    "api integration": 9, "system integration": 8, "cybersecurity": 8,
    "cyber security": 8, "software": 6, "web portal": 7,
    "website": 6, "cloud services": 6, "database": 5,
    "networking": 4, "information technology": 4,
    "it equipment": 3, "computer hardware": 3, "laptops": 2,
    "digital": 2, "video surveillance": 4,
}

def plain(value):
    return " ".join(value.split())

def parse_deadline(value):
    if not value:
        return None
    found = DATE.search(value)
    if not found:
        return None
    s = plain(found.group(0))
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p",
                "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None

def parse_list(html):
    """Expected PPRA columns: #, reference, details, buyer, status, advertised, closing."""
    soup = BeautifulSoup(html, "html.parser")
    notices = []
    for row in soup.select("tr"):
        cells = [plain(t.get_text(" ", strip=True)) for t in row.select("td")]
        if len(cells) < 7:
            continue
        ref = ID.search(cells[1])
        if not ref:
            continue
        tid = ref.group(0).upper()
        ad = DATE.search(cells[5])
        close = DATE.search(cells[6])
        notices.append({
            "tender_id": tid, "title": cells[2][:500],
            "organization": cells[3][:250], "status": cells[4],
            "advertised": ad.group(0) if ad else None,
            "closing": close.group(0) if close else None,
            "source_url": BASE + "/public/tenders/tender-details/" + tid,
        })
    return notices

def shortlist(html_pages, now):
    all_notices = {}
    for html in html_pages:
        for n in parse_list(html):
            all_notices[n["tender_id"]] = n
    matches = []
    for n in all_notices.values():
        deadline = parse_deadline(n["closing"])
        if not deadline or deadline <= now:
            continue
        if re.search(r"\b(?:cancelled|canceled|withdrawn)\b", n["status"], re.I):
            continue
        searchable = (n["title"] + " " + n["organization"]).lower()
        tags = [k for k in KEYWORDS if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", searchable)]
        if not tags:
            continue
        n["matched_terms"] = tags
        n["score"] = sum(KEYWORDS[k] for k in tags)
        matches.append(n)
    matches.sort(key=lambda n: (-n["score"], parse_deadline(n["closing"]), n["tender_id"]))
    return matches, len(all_notices)

def render(items, scanned, now, folder):
    folder.mkdir(parents=True, exist_ok=True)
    result = {
        "generated_at_pakistan": now.isoformat(timespec="seconds"),
        "source": LIST_URL, "scanned": scanned, "matched": len(items),
        "warning": "Research shortlist only. Confirm scope, amendments and eligibility with the official notice.",
        "tenders": items,
    }
    (folder / "latest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Pakistan IT Tender Radar", "",
        f"Generated: {now:%Y-%m-%d %H:%M} PKT", "",
        f"{len(items)} keyword-matched notices with future closing dates among {scanned} inspected notices.",
        "", "Independent research. Verify each official tender before bidding.", "",
    ]
    for i, n in enumerate(items[:30], 1):
        md += [
            f"## {i}. {n['title'][:150]}", "",
            f"- Tender: {n['tender_id']}",
            f"- Organization: {n['organization']}",
            f"- Closing (Pakistan time, portal listing): {n['closing']}",
            f"- Matches: {', '.join(n['matched_terms'])}",
            f"- Source: {n['source_url']}", "",
        ]
    if not items:
        md += ["No IT keyword matches found within the scanned pages.", ""]
    (folder / "latest.md").write_text("\n".join(md), encoding="utf-8")

def fetch(page, timeout=20):
    req = Request(
        LIST_URL + f"?page={page}",
        headers={"User-Agent": "TenderRadarResearch/0.1 (public-data pilot)", "Accept": "text/html"},
    )
    with urlopen(req, timeout=timeout) as res:
        if "html" not in res.headers.get("Content-Type", "").lower():
            raise ValueError("PPRA response is not HTML")
        return res.read(4_000_000).decode("utf-8", errors="replace")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--html-input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("tender_radar/data"))
    args = parser.parse_args()
    if args.pages < 1 or args.pages > 5:
        parser.error("--pages must be 1..5")
    now = datetime.now(TZ)
    pages = []
    if args.html_input:
        pages = [args.html_input.read_text(encoding="utf-8")]
    else:
        for p in range(1, args.pages + 1):
            pages.append(fetch(p))
            if p != args.pages:
                time.sleep(1)
    records, scanned = shortlist(pages, now)
    if not scanned:
        raise RuntimeError("No tender rows parsed. Preserve previous successful report.")
    render(records, scanned, now, args.output_dir)
    print(f"Scanned {scanned} unique notices; matched {len(records)} IT notices.")

if __name__ == "__main__":
    main()
