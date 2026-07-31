# Mobile Developer Job Tracker — setup

Three files:

| File | What it does |
|---|---|
| `fetch_jobs.py` | Pulls jobs from ~30 sources, writes `jobs.json`. Python 3, **no dependencies**. |
| `live_mobile_job_tracker.html` | The page you open. Reads `jobs.json` + queries 3 live APIs. |
| `job-refresh.yml` | GitHub Actions workflow for automatic daily refresh. |

---

## Quick start (manual)

Put all files in one folder, then:

```bash
python3 fetch_jobs.py          # writes jobs.json
```

Then **double-click `live_mobile_job_tracker.html`** to open it in your browser.

Run the script again whenever you want fresh listings, and hit **↻ Refresh jobs** in the page.

> Opening the file directly matters: `file://` lets it read `jobs.json` and save your
> checkboxes. If you open it some other way, fetching or saved progress may be blocked.

If `jobs.json` fails to load from `file://` in Chrome (it blocks local file reads on some
versions), serve the folder instead:

```bash
python3 -m http.server 8000
# then visit http://localhost:8000/live_mobile_job_tracker.html
```

---

## Option A — Daily automatic refresh on your own machine (cron)

macOS / Linux. Edit your crontab:

```bash
crontab -e
```

Add one line (runs 07:00 daily — adjust the path):

```
0 7 * * * cd /full/path/to/jobfolder && /usr/bin/python3 fetch_jobs.py >> refresh.log 2>&1
```

Windows: use Task Scheduler → Create Basic Task → Daily → Start a program
→ `python` with argument `fetch_jobs.py`, "Start in" = your folder.

---

## Option B — GitHub Actions (runs in the cloud, works from your phone)

1. Create a GitHub repo and push all three files, renaming the workflow:

```bash
mkdir -p .github/workflows
mv job-refresh.yml .github/workflows/
git add . && git commit -m "job tracker" && git push
```

2. In the repo: **Settings → Actions → General → Workflow permissions** →
   select **Read and write permissions** (so it can commit `jobs.json`).

3. Optional, to view it on your phone: **Settings → Pages → Source = GitHub Actions**.
   After the first run your tracker is live at
   `https://<your-username>.github.io/<repo>/`

4. Trigger it once manually: **Actions → Refresh job listings → Run workflow**.

It then runs itself every day at 06:00 UTC.

---

## Adding more employers

The highest-value source is companies' own job boards. Find a company's ATS and add its
slug to the `ATS` dict in `fetch_jobs.py`:

```python
ATS = {
    "greenhouse": ["n26", "adyen", "traderepublicbank", ...],
    "lever":      ["moonpay", "spotify", ...],
    "ashby":      ["miro", "hostinger", ...],
    "smartrec":   ["deliveryhero", "wise", "picnic", ...],
}
```

To discover which ATS a company uses, open its careers page and look at the URL, or test:

```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/SLUG/jobs" | head -c 200
curl -s "https://api.lever.co/v0/postings/SLUG?mode=json"      | head -c 200
curl -s "https://api.ashbyhq.com/posting-api/job-board/SLUG"   | head -c 200
curl -s "https://api.smartrecruiters.com/v1/companies/SLUG/postings" | head -c 200
```

Anything that returns JSON with jobs is a valid slug.

---

## Tuning the filters

In `fetch_jobs.py`:

- `STRONG_RE` — unambiguously mobile keywords. Kotlin/Swift/Dart are deliberately *not*
  here, because they're also server-side languages; a title matching only those plus a
  backend/fullstack word gets dropped.
- `EXCLUDE_RE` — non-engineering roles to discard.
- `elig_of()` — the Tunisia-eligibility heuristic. **This is a guess based on the region
  string each source reports.** "Worldwide" often hides country exclusions, so always
  confirm on the actual posting.
- The 45-day freshness cutoff is in `main()`.

---

## Honest limitations

- **LinkedIn, Indeed and Glassdoor are not included.** They block automated access and
  scraping them breaks their terms of service. They hold most listings, so keep checking
  them manually — the links at the bottom of the page are there for that.
- **Expect roughly 20–40 mobile roles**, not hundreds. Of ~3,200 raw postings gathered on
  the last test run, 24 were genuine mobile engineering roles. That's the honest size of
  what's publicly available through open sources on any given day.
- **Eligibility and seniority tags are inferred from job titles and region strings.**
  They're triage aids, not facts.
- **ATS slugs drift.** If a company reorganises, its endpoint 404s and the script silently
  skips it — the run output lists which companies failed, so check it occasionally.
- Some sources are intermittently down (Arbeitnow returned 503 during testing). The script
  and page both degrade gracefully rather than failing.
