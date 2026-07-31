#!/usr/bin/env python3
"""
fetch_jobs.py — daily mobile-developer job aggregator.

Pulls from three legitimate, public source types (no ToS-violating scraping):
  1. ATS public job-board APIs (Greenhouse / Lever / Ashby / SmartRecruiters)
     -> this is where the visa-sponsoring employers actually publish.
  2. Remote job APIs (Remotive, Jobicy, Arbeitnow).
  3. Public RSS feeds (Working Nomads, Himalayas, We Work Remotely, Jobicy).

Writes jobs.json next to this script, which live_mobile_job_tracker.html reads.

Usage:  python3 fetch_jobs.py
"""

import json, re, sys, time, urllib.request, urllib.error, pathlib
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

OUT = pathlib.Path(__file__).parent / "jobs.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; personal-job-tracker)",
      "Accept": "application/json, application/xml, text/xml, */*"}
TIMEOUT = 25

# ---------------------------------------------------------------- classifiers
MOBILE_RE  = re.compile(r"(flutter|dart\b|react[\s-]?native|android|\bios\b|kotlin|swift"
                        r"|jetpack compose|mobile|expo\b|kotlin multiplatform|\bkmp\b)", re.I)
# "Strong" = unambiguously mobile. Kotlin/Swift/Dart alone are NOT: they are also
# common server-side languages, so "Senior Backend Engineer (Java/Kotlin)" matched before.
STRONG_RE  = re.compile(r"(flutter|react[\s-]?native|android|\bios\b|mobile|expo\b"
                        r"|jetpack compose|swiftui|kotlin multiplatform|\bkmp\b)", re.I)
NONMOBILE_CTX_RE = re.compile(r"\b(backend|back[\s-]end|fullstack|full[\s-]stack|server[\s-]side"
                              r"|devops|sre|data engineer|platform engineer|cloud engineer)\b", re.I)
DEVROLE_RE = re.compile(r"(engineer|developer|programmer|architect|tech lead|team lead|sdk)", re.I)
EXCLUDE_RE = re.compile(r"\b(designer|graphic|ux designer|ui designer|marketing|sales|recruiter|"
                        r"copywriter|accountant|content writer|customer (support|success)|seo|"
                        r"data scientist|qa engineer|tester|technical writer)\b", re.I)
VISA_RE    = re.compile(r"(visa sponsor|sponsorship|relocation (package|support|assistance|bonus)|"
                        r"blue card|work permit|we sponsor|relocate)", re.I)

def stack_of(text):
    s = text.lower()
    if re.search(r"flutter|dart", s):            return "Flutter"
    if re.search(r"react[\s-]?native|expo", s):  return "React Native"
    if re.search(r"android|kotlin", s):          return "Android"
    if re.search(r"\bios\b|swift", s):           return "iOS"
    return "Mobile"

def level_of(t):
    s = t.lower()
    if re.search(r"\b(lead|principal|staff|head of|architect|manager|director|vp)\b", s): return "Lead"
    if re.search(r"\b(senior|sr\.?|snr)\b", s):                                          return "Senior"
    if re.search(r"\b(junior|jr\.?|intern|graduate|entry|trainee|working student|"
                 r"werkstudent|praktikum|apprentice)\b", s):                             return "Junior"
    return "Mid"

def elig_of(geo):
    """Heuristic: can a Tunisia-based candidate plausibly take this remotely?"""
    g = (geo or "").lower()
    if not g:                                                    return "m", "Unstated"
    if re.search(r"anywhere|worldwide|global|remote - global", g):return "y", "Worldwide"
    if "africa" in g:                                            return "y", "Africa incl."
    if re.search(r"emea|middle east", g):                        return "y", "EMEA"
    if re.search(r"\beurope\b", g):                              return "m", geo[:44]
    if re.search(r"usa|united states|u\.s\.|americas|canada|latam|brazil|mexico|apac|"
                 r"australia|india|japan|singapore", g):         return "n", geo[:44]
    return "m", geo[:44]

def norm_key(company, title):
    t = re.sub(r"\(.*?\)", "", title or "")
    return re.sub(r"[^a-z0-9]", "", ((company or "") + "|" + t).lower())[:90]

def iso(dt):
    if not dt: return None
    if isinstance(dt, (int, float)):
        try:
            # Lever (and some others) return epoch MILLISECONDS
            if dt > 1e11: dt = dt / 1000.0
            return datetime.fromtimestamp(dt, timezone.utc).isoformat()
        except Exception: return None
    s = str(dt).strip()
    for f in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ",
              "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z",
              "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            d = datetime.strptime(s.replace("+00:00", "+0000"), f)
            if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc).isoformat()
        except Exception: pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return (m.group(1) + "T00:00:00+00:00") if m else None

def fetch(url, as_json=True):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return json.loads(raw) if as_json else raw.decode("utf-8", "ignore")

# ---------------------------------------------------------------- ATS sources
# Verified public endpoints. Add companies freely — slug is the only thing needed.
ATS = {
    "greenhouse": ["n26", "adyen", "traderepublicbank", "hellofresh", "canonical", "celonis",
                   "gitlab", "coinbase", "solarisbank", "raisin", "contentful"],
    "lever":      ["moonpay", "spotify"],
    "ashby":      ["miro", "hostinger", "docplanner", "clark", "zenjob", "forto", "choco"],
    "smartrec":   ["deliveryhero", "wise", "picnic"],
}
ATS_URL = {
    "greenhouse": lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever":      lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json",
    "ashby":      lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{s}",
    "smartrec":   lambda s: f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100",
}

def parse_ats(kind, slug, d):
    out = []
    if kind == "greenhouse":
        for j in d.get("jobs", []):
            out.append(dict(company=slug.title(), title=j.get("title", ""),
                            geo=(j.get("location") or {}).get("name", ""),
                            date=iso(j.get("updated_at")), url=j.get("absolute_url", ""),
                            salary="", src=f"{slug} (Greenhouse)"))
    elif kind == "lever":
        for j in d if isinstance(d, list) else []:
            c = j.get("categories") or {}
            out.append(dict(company=slug.title(), title=j.get("text", ""),
                            geo=c.get("location", ""), date=iso(j.get("createdAt")),
                            url=j.get("hostedUrl", ""), salary="", src=f"{slug} (Lever)"))
    elif kind == "ashby":
        for j in d.get("jobs", []):
            out.append(dict(company=slug.title(), title=j.get("title", ""),
                            geo=j.get("location", ""), date=iso(j.get("publishedAt")),
                            url=j.get("jobUrl") or j.get("applyUrl", ""), salary="",
                            src=f"{slug} (Ashby)"))
    elif kind == "smartrec":
        for j in d.get("content", []):
            loc = j.get("location") or {}
            geo = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
            out.append(dict(company=slug.title(), title=j.get("name", ""), geo=geo,
                            date=iso(j.get("releasedDate")),
                            url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id','')}",
                            salary="", src=f"{slug} (SmartRecruiters)"))
    return out

# ---------------------------------------------------------------- remote APIs
def src_remotive():
    rows, qs = [], ["flutter", "react%20native", "mobile%20developer", "mobile%20engineer",
                    "android", "ios", "kotlin", "swift"]
    for q in qs:
        try:
            for j in fetch(f"https://remotive.com/api/remote-jobs?search={q}&limit=80").get("jobs", []):
                rows.append(dict(company=j.get("company_name", ""), title=j.get("title", ""),
                                 geo=j.get("candidate_required_location", ""),
                                 date=iso(j.get("publication_date")), url=j.get("url", ""),
                                 salary=j.get("salary") or "", src="Remotive"))
        except Exception: pass
    return rows

def src_jobicy():
    rows, tags = [], ["flutter", "react-native", "mobile", "mobile-development",
                      "android", "ios", "kotlin", "swift"]
    for t in tags:
        try:
            for j in fetch(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={t}").get("jobs", []):
                sal = ""
                if j.get("salaryMin") and j.get("salaryMax"):
                    try:
                        sal = (f"{round(float(j['salaryMin'])/1000)}–{round(float(j['salaryMax'])/1000)}k "
                               f"{j.get('salaryCurrency','')}").strip()
                    except Exception: pass
                rows.append(dict(company=j.get("companyName", ""), title=j.get("jobTitle", ""),
                                 geo=j.get("jobGeo", ""), date=iso(j.get("pubDate")),
                                 url=j.get("url", ""), salary=sal, src="Jobicy"))
        except Exception: pass
    return rows

def src_arbeitnow():
    rows = []
    for page in (1, 2, 3):
        try:
            d = fetch(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
            for j in d.get("data", []):
                desc = j.get("description", "") or ""
                rows.append(dict(company=j.get("company_name", ""), title=j.get("title", ""),
                                 geo=("Remote / " + (j.get("location") or "EU")) if j.get("remote")
                                      else (j.get("location") or "Germany"),
                                 date=iso(j.get("created_at")), url=j.get("url", ""),
                                 salary="", src="Arbeitnow (DE/EU)",
                                 visa=bool(VISA_RE.search(desc))))
        except Exception: pass
    return rows

# ---------------------------------------------------------------- RSS sources
RSS = {
    "Himalayas":        "https://himalayas.app/jobs/rss",
    "We Work Remotely": "https://weworkremotely.com/remote-jobs.rss",
    "WWR Programming":  "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "Jobicy RSS":       "https://jobicy.com/?feed=job_feed",
}

def _rss_tag(block, t):
    m = re.search(rf"<{t}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", block, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

def src_rss(name, url):
    """Regex-based: several job feeds emit malformed XML (unbound namespace
    prefixes), which strict parsers reject outright."""
    rows = []
    try:
        body = fetch(url, as_json=False)
    except Exception:
        return rows
    for block in re.findall(r"<item[ >].*?</item>", body, re.S):
        title = _rss_tag(block, "title")
        link  = _rss_tag(block, "link")
        if not title or not link:
            continue
        company, role = "", title
        # feeds format variously as "Company: Role" or "Role at Company"
        if ":" in title:
            a, b = title.split(":", 1)
            if len(a) < 45:
                company, role = a.strip(), b.strip()
        elif " at " in title:
            b, a = title.rsplit(" at ", 1)
            company, role = a.strip(), b.strip()
        # Himalayas puts the company in the URL path, not the title
        if not company:
            m = re.search(r"/companies/([^/]+)", link)
            if m:
                company = m.group(1).replace("-", " ").title()
        desc = re.sub(r"<[^>]+>", " ", _rss_tag(block, "description"))[:1500]
        rows.append(dict(company=company or name, title=role,
                         geo=_rss_tag(block, "region") or _rss_tag(block, "location") or "",
                         date=iso(_rss_tag(block, "pubDate")), url=link, salary="",
                         src=name, visa=bool(VISA_RE.search(desc))))
    return rows

# ---------------------------------------------------------------- orchestrate
def gather():
    tasks, stats = [], {}

    def run_ats(kind, slug):
        try:
            return parse_ats(kind, slug, fetch(ATS_URL[kind](slug))), f"{slug}"
        except Exception:
            return [], f"{slug}!"

    with ThreadPoolExecutor(16) as ex:
        futs = []
        for kind, slugs in ATS.items():
            for s in slugs:
                futs.append(ex.submit(run_ats, kind, s))
        f_api = [ex.submit(src_remotive), ex.submit(src_jobicy), ex.submit(src_arbeitnow)]
        f_rss = [ex.submit(src_rss, n, u) for n, u in RSS.items()]

        ats_rows, ats_ok, ats_fail = [], 0, []
        for f in futs:
            rows, tag = f.result()
            if rows: ats_ok += 1
            elif tag.endswith("!"): ats_fail.append(tag[:-1])
            ats_rows += rows
        api_rows = []
        for f, nm in zip(f_api, ["Remotive", "Jobicy", "Arbeitnow"]):
            r = f.result(); api_rows += r; stats[nm] = len(r)
        rss_rows = []
        for f, nm in zip(f_rss, RSS.keys()):
            r = f.result(); rss_rows += r; stats[nm] = len(r)

    stats["ATS companies OK"] = ats_ok
    if ats_fail: stats["ATS failed"] = ",".join(ats_fail)
    return ats_rows + api_rows + rss_rows, stats

def refine(raw):
    seen, out = set(), []
    for j in raw:
        title, company = (j.get("title") or "").strip(), (j.get("company") or "").strip()
        if not title or not company or not j.get("url"): continue
        if not MOBILE_RE.search(title):  continue
        if not DEVROLE_RE.search(title): continue
        if EXCLUDE_RE.search(title):     continue
        # Kotlin/Swift/Dart alone + a backend/fullstack context = server role, not mobile
        if not STRONG_RE.search(title) and NONMOBILE_CTX_RE.search(title): continue
        k = norm_key(company, title)
        if k in seen: continue
        seen.add(k)
        ec, el = elig_of(j.get("geo"))
        out.append(dict(company=company, title=title, stack=stack_of(title),
                        level=level_of(title), geo=j.get("geo") or "",
                        eligCode=ec, eligLabel=el, salary=j.get("salary") or "",
                        date=j.get("date"), url=j["url"], src=j.get("src", ""),
                        visa=bool(j.get("visa")) or bool(VISA_RE.search(title))))
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    return out

def main():
    t0 = time.time()
    raw, stats = gather()
    jobs = refine(raw)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    fresh = [j for j in jobs if not j["date"] or j["date"] >= cutoff]
    payload = dict(generatedAt=datetime.now(timezone.utc).isoformat(),
                   totalRaw=len(raw), totalJobs=len(fresh), sources=stats, jobs=fresh)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    print(f"raw rows      : {len(raw)}")
    print(f"mobile roles  : {len(jobs)}  (kept <=45 days: {len(fresh)})")
    print(f"stacks        : {dict(Counter(j['stack'] for j in fresh))}")
    print(f"levels        : {dict(Counter(j['level'] for j in fresh))}")
    print(f"eligibility   : {dict(Counter(j['eligCode'] for j in fresh))}")
    print(f"visa-flagged  : {sum(1 for j in fresh if j['visa'])}")
    print(f"sources       : {stats}")
    print(f"wrote         : {OUT}  ({time.time()-t0:.1f}s)")

if __name__ == "__main__":
    sys.exit(main())
