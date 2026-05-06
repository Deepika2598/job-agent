# Personalized Job Hunting Agent

An automated agent that finds fresh data engineering jobs (filtered for visa sponsorship, no clearance), tailors your resume per role, drafts cold emails, and notifies you — running every 15 minutes from 7 AM to 8 PM EST on free infrastructure.

## What it does

1. **Every 15 minutes** (7 AM – 8 PM EST), pulls jobs from multiple free sources
2. **Filters** for: data engineering roles, posted recently, no "US citizens only / GC required / active clearance" language, sponsorship-friendly signals
3. **Deduplicates** against jobs already seen (SQLite)
4. **For each new match**: generates a tailored `.docx` resume + a draft cold email
5. **Notifies you** by email (or Telegram) with the job link, resume file, and email draft
6. **You** click apply, attach the generated resume, send the email. ~90 seconds per role.

## Architecture (all free tier)

```
GitHub Actions cron (every 15 min, 7am-8pm EST)
        │
        └─> main.py
              │
              ├─> job_fetcher.py     → Greenhouse, Lever, Ashby, RemoteOK, Adzuna
              ├─> job_filter.py      → keyword + sponsorship + clearance filtering
              ├─> database.py        → SQLite dedup
              ├─> resume_tailor.py   → Gemini API (free tier: 15 RPM)
              ├─> resume_generator.py → python-docx, ATS-friendly
              ├─> cold_email.py      → Gemini-drafted cold email + Hunter.io lookup
              └─> notifier.py        → Gmail SMTP or Telegram bot
```

## Why I'm NOT auto-applying

Auto-apply violates LinkedIn / Indeed / most ATS terms of service and gets accounts banned. It also doesn't work reliably — every company uses a different system (Workday, Greenhouse, Lever, iCIMS, custom). The leverage isn't in clicking submit; it's in spending 90 seconds per application instead of 20 minutes. This agent gets you to that 90-second point.

## Resume formatting

The generator matches your existing resume template exactly:

| Element | Spec |
|---|---|
| Page | A4 (11906 × 16838 twips) |
| Margins | 0.75" top/bottom, 0.875" left/right |
| Font | Times New Roman throughout |
| Body | 13pt, justified |
| Name | 18pt bold, navy `#1F3864`, centered, ALL CAPS |
| Subtitle + contact | 13pt bold, navy, centered |
| Section headers | 13pt bold navy, with trailing colon and a thin navy `#2E5FA3` bottom border |
| Bullets | ● (Times New Roman filled circle) with hanging indent |
| Skills | 2-column table with light-gray `#CCCCCC` cell borders |
| Each role ends with | `Environment: <comma-separated tech list>` |

### Inline bold keywords

Your existing resume bolds specific keywords mid-sentence (e.g., "across **AWS** and **Microsoft Azure** cloud platforms"). The pipeline preserves this:

- `resume_data.json` uses `**double-asterisk**` markers around words that should render bold
- The Gemini tailoring prompt is instructed to **preserve** existing markers AND **add 1–3 new ones per bullet** around JD-critical keywords (Snowflake-heavy role → `**Snowflake**` bolded automatically; Airflow role → `**Apache Airflow**`)
- Capped at 2–4 emphasized phrases per bullet so it doesn't turn into a wall of bold
- The generator's `_add_runs_with_inline_bold()` parses the markers and emits properly-formatted runs

## Setup (one-time, ~30 minutes)

### 1. Get your free API keys

| Service | What for | Free tier | Sign up |
|---|---|---|---|
| **Google Gemini** | Resume tailoring, cold emails | 15 req/min, 1500/day | https://aistudio.google.com/apikey |
| **Adzuna** | Job listings (broader catch-all) | 1000 calls/month | https://developer.adzuna.com/signup |
| **Hunter.io** *(optional)* | Hiring manager emails | 25 lookups/month | https://hunter.io |
| **Gmail App Password** | Send notifications to yourself | Free | https://myaccount.google.com/apppasswords |

Greenhouse, Lever, Ashby, and RemoteOK have public APIs — no key needed.

### 2. Fork / clone this repo to your GitHub

```bash
git clone <this-repo>
cd job-agent
```

### 3. Set up GitHub Secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

Add these:
- `GEMINI_API_KEY`
- `ADZUNA_APP_ID`
- `ADZUNA_API_KEY`
- `HUNTER_API_KEY` *(optional)*
- `NOTIFY_EMAIL` (where you want notifications sent)
- `GMAIL_USER` (Gmail address that sends notifications)
- `GMAIL_APP_PASSWORD` (the 16-char app password, not your real Gmail password)

### 4. Configure your search

Edit `config.yaml`:

- `search.keywords` — title patterns to look for ("data engineer", "senior data engineer", etc.)
- `search.locations` — cities, states, or "remote"
- `search.max_age_hours` — how fresh a posting must be (default: 24)
- `filters.exclude_phrases` — already pre-filled with citizenship/clearance terms; add more if you see them slip through
- `filters.positive_phrases` — sponsorship-friendly signals that boost score
- `filters.priority_companies` — companies you'd love to hear about first
- `sources.greenhouse.boards` / `sources.lever.boards` / `sources.ashby.boards` — company slugs (the URL fragment after `boards.greenhouse.io/`)
- `tailoring.mode` — `conservative` | `moderate` | `aggressive`

### 5. Update `resume_data.json` if needed

It's pre-filled from your uploaded `.docx`, including the `**bold markers**` for keywords. Add `linkedin` / `github` URLs to the `contact` section if you want them in the header. If you want different keywords bolded by default, edit the markers in the summary bullets.

### 6. Push to GitHub

```bash
git add .
git commit -m "configure my agent"
git push
```

The GitHub Action runs automatically on schedule. To test: **Actions tab → Job Agent → Run workflow** to trigger manually.

## Running locally (for testing)

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env with your keys
python -m src.main
```

Generated artifacts land in `data/output/<timestamp>/` — one tailored `.docx` per job, one matching `email_<slug>.txt` with subject and body.

## File structure

```
job-agent/
├── README.md                          # this file
├── requirements.txt                   # Python deps
├── config.yaml                        # your search prefs
├── resume_data.json                   # your structured resume (with **bold** markers)
├── .env.example                       # local env template
├── .gitignore
├── .github/workflows/agent.yml        # scheduled runner
├── data/
│   ├── jobs.db                        # SQLite (auto-created, persisted via Actions cache)
│   └── output/                        # tailored resumes + emails per run
└── src/
    ├── __init__.py
    ├── main.py                        # orchestrator
    ├── job_fetcher.py                 # multi-source fetching
    ├── job_filter.py                  # filter logic
    ├── resume_tailor.py               # Gemini-powered tailoring (preserves **bold**)
    ├── resume_generator.py            # docx generation matching your template
    ├── cold_email.py                  # email drafting + hiring manager lookup
    ├── notifier.py                    # send notifications (email/Telegram)
    └── database.py                    # SQLite tracking
```

## Tweaking the resume format

Open `src/resume_generator.py`. The constants at the top are the knobs:

```python
FONT = "Times New Roman"
BODY_PT = 13
NAME_PT = 18
ACCENT = RGBColor(0x1F, 0x38, 0x64)       # name + section header text
UNDERLINE_HEX = "2E5FA3"                  # section header bottom border
TABLE_BORDER_HEX = "CCCCCC"               # skills table borders
```

Other tweaks:

- **Skills column widths**: `_skills_block()` → `col_widths = [Inches(1.85), Inches(4.65)]`
- **Bullet character**: `_add_bullet()` → change `"●\t"` to `"•\t"` or `"▪\t"`
- **Section header underline thickness**: `_add_bottom_border()` → `size="8"` (in eighths of a point — bump to 12 for thicker)
- **Indent depth on bullets**: `_add_bullet()` → `Inches(0.3)` controls both the left indent and the hanging indent

## Realistic expectations

- **First few days**: noisy. You'll need to tune `config.yaml` keyword filters based on what comes through. Add anything weird to `filters.exclude_phrases`.
- **Hiring manager email accuracy**: ~30-60% with pattern guessing alone, ~80% if Hunter.io has the company. Always sanity-check before sending. The `cold_email.py` module marks confidence as `low`/`medium`/`high` so you know what to trust.
- **Resume tailoring quality**: Gemini does a good job at re-ordering bullets and emphasizing relevant skills. The prompt has hard rules — it will **not** invent companies, dates, metrics, or technologies. If you ever see a tailored resume claim something you didn't do, that's a bug worth flagging.
- **GitHub Actions cost**: Free for public repos. For private repos, this fits in the 2000 min/month free tier as long as each run stays under ~1 minute (typical run: 20-40 sec).
- **API quotas**: Gemini's free tier (1500 req/day) is plenty. Adzuna's 1000/month with one call per run = ~33 days at 30 runs/day; if you exceed it, the source just gets skipped silently.

## When to hand-write vs let the agent draft

**Always hand-edit:**
- Cold emails to hiring managers at companies you really want
- Cover letters for senior roles ($150k+) — the personal touch matters
- Anything where the JD is unusual or the company is small

**Let the agent run unedited:**
- Standard portal applications (Workday, Greenhouse) where the resume does the talking
- Cold emails to recruiters (volume game)

## Troubleshooting

**"No new jobs found" every run**
Either filters are too tight or your company-board lists are too narrow. Check `data/output/` logs in the GitHub Actions run, look at the rejection counts (`{keyword: N, location: N, exclude: N}`) — that tells you which filter is dropping things. Often the fix is adding more boards to `sources.greenhouse.boards`.

**Email not arriving**
Gmail App Password must be from the same Gmail account as `GMAIL_USER`. Two-factor auth must be enabled on that account first, otherwise app passwords aren't available. Check the Actions log for SMTP errors.

**Resume formatting looks off**
Generate a sample locally with `python -c "import json; from src.resume_generator import generate_docx; generate_docx(json.load(open('resume_data.json')), 'sample.docx')"` and inspect. The constants at the top of `resume_generator.py` are easy to tune.

**Gemini hits a rate limit**
Free tier is 15 RPM / 1500 RPD. If you're getting 10+ matches per run, add a small `time.sleep(4)` between calls in `main.py`'s loop. Almost never hit in practice.
