# Job Alert Filter & Salary Ranker 📬💼

A high-performance, modular CLI toolkit to automate the ingestion of job alert emails, split multi-job digest emails (LinkedIn, Naukri, etc.), normalize compensations across global currencies (USD, INR/LPA), rank roles by dream company tiers and position preferences, and display clean, prioritized summaries.

---

## 🏗 Architecture & Data Pipeline

```
┌─────────────────────────────────┐
│       1. INGESTION LAYER        │
│                                 │
│  [ Gmail API ]   [ Local Files ]│
│  download_emails.py alerts.txt  │
│         │         emails/*.eml  │
└─────────┼───────────────┼───────┘
          │ (saves .eml)  │
          ▼               ▼
┌─────────────────────────────────┐
│     2. EXTRACTION & PARSING     │
│  - Multi-job digest splitter    │
│    (LinkedIn, Naukri, etc.)     │
│  - Role, Company, URL regex     │
│  - Multi-Currency Normalizer    │
│    ($180k, $75/hr, 18-24 LPA)   │
└─────────────────┬───────────────┘
                  ▼
┌─────────────────────────────────┐
│     3. PREFERENCES & RANKING    │
│  - Company Tiers (Tier 1..N)    │
│  - Position Preferences (#1..N) │
│  - Target Salary & Glassdoor    │
│    Ratings (stars & reviews)    │
└─────────────────┬───────────────┘
                  ▼
┌─────────────────────────────────┐
│     4. FILTERING & REPORTING    │
│  - Frontend / Custom keywords   │
│  - Min-salary threshold filter  │
│  - Priority sort: Co. Tier ──▶  │
│    Pos. Rank ──▶ Salary         │
│  - Terminal Table / MD / CSV    │
└─────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Requirements & Setup
- **Python 3.10+**
- Core engine (`job_alert_filter.py`) uses **standard library only** (no external packages required).
- Gmail downloader (`download_emails.py`) requires Google API client libraries.

```bash
# Clone the repository
git clone https://github.com/your-username/job_alert_filter.git
cd job_alert_filter

# (Optional) Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies if using Gmail downloader
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

### 2. Test Run with Included Sample Alerts
```bash
python3 job_alert_filter.py -i alerts.txt
```

---

## 📥 Automated Email Ingestion (`download_emails.py`)

The repository includes `download_emails.py`, a dedicated Gmail API ingestion tool that downloads job alert messages via OAuth 2.0 and saves them as standard `.eml` files into an `emails/` directory.

### Step 1: Google Cloud Setup
1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project, then enable the **Gmail API**.
3. Go to **APIs & Services > Credentials**, create an **OAuth 2.0 Client ID** (select Application Type: **Desktop app**).
4. Download the client secrets JSON file and place it in the project root as **`credentials.json`**.

### Step 2: Run the Downloader
On first run, `download_emails.py` opens a browser window for Google authentication and saves your session token to `token.json` for seamless future runs:

```bash
# Download job alerts from the past 1 day (default: emails/ directory)
python3 download_emails.py --days 1

# Download job alerts from the past 3 days
python3 download_emails.py --days 3

# Fetch using a custom Gmail search query
python3 download_emails.py --query 'newer_than:2d (from:linkedin.com OR from:naukri.com OR "job alert")'

# Specify custom output folder and maximum email limit
python3 download_emails.py --days 2 --output my_emails --max-results 100
```

#### CLI Options for `download_emails.py`:
| Flag | Default | Description |
|---|---|---|
| `--days` | `1` | Number of past days to query (`newer_than:<days>d`). |
| `--query` | `None` | Custom Gmail search query (overrides `--days`). |
| `-o`, `--output` | `emails` | Directory where downloaded `.eml` files are stored. |
| `--max-results` | `50` | Maximum number of emails to retrieve per run. |
| `--credentials` | `credentials.json` | Path to your OAuth 2.0 client secret file. |
| `--token` | `token.json` | Path to the persistent OAuth token cache. |

### Step 3: Parse and Rank Downloaded Emails
Once emails are downloaded, feed the directory directly to `job_alert_filter.py`:

```bash
python3 job_alert_filter.py -i emails/
```

---

## 🧩 Multi-Job Digest & Multi-Platform Parsing

Job platforms rarely send one email per job. Instead, they send daily digests containing multiple listings. `job_alert_filter.py` automatically detects and splits these digests into discrete `JobAlert` items:

- **LinkedIn Job Alerts**: Parses multi-role digest cards, extracting individual job titles, company names, locations, and direct application URLs (`https://www.linkedin.com/comm/jobs/view/...`).
- **Naukri Alerts & Recommendations**: Parses card blocks with experience requirements, INR/LPA salaries, company names, and apply links (`https://dl.naukri.com/apply...`).
- **Direct Recruiter Emails & Plain Alerts**: Parses single-job notification emails, raw `.eml` files, or standard plain text dumps (`alerts.txt`).

---

## 💰 Multi-Currency & Compensation Normalization

The parser automatically recognizes, parses, and normalizes compensation into annual figures across multiple currencies:

| Format in Email | Normalized Output | Sort Key Calculation |
|---|---|---|
| `$210,000 - $260,000 / yr` | `$210,000 - $260,000/yr` | Midpoint: `$235,000` |
| `$180k` | `$180,000/yr` | `$180,000` |
| `$75 - $95 / hr` | `$156,000 - $197,600/yr` | Based on 2,080 annual hours |
| `$12,000 / month` | `$144,000/yr` | Based on 12 months |
| `15 - 25 LPA` | `₹15,00,000 - ₹25,00,000/yr` | Indian Lakhs per Annum (INR) |
| `₹18,00,000 / year` | `₹1,800,000/yr` | Standard INR formatting |
| *Unlisted* | `Unlisted` | Defaults to bottom sort tier |

---

## 🎯 Company & Position Preference Ranking (`rankings.csv`)

In addition to salary, the tool lets you prioritize opportunities according to **company tiers**, **position titles**, **target pay**, and **company ratings/reviews**.

### CSV Schema (`rankings.csv`):
```csv
category,name,rank,salary,rating,number_of_ratings
company,Stripe,1,$220k - $260k,4.3,12500
company,Vercel,1,$150k - $190k,4.5,850
company,HyperScale AI,2,$160k - $200k,4.0,120
company,Creative Studio Inc,3,$120k - $150k,3.5,45
position,Staff Front-End Architect,1,$210k - $260k,,
position,Lead UI Engineer,1,$160k - $200k,,
position,Senior Frontend Engineer,2,$140k - $180k,,
```

### Ranking & Sorting Hierarchy
When ranking is active, jobs are sorted deterministically using the following priorities:
1. **Company Rank (Ascending)**: Tier 1 dream companies appear before Tier 2, etc. (Unranked = Tier 99).
2. **Position Rank (Ascending)**: Preferred role titles (#1) appear before secondary roles (#2).
3. **Compensation (Descending)**: Within the same company tier and position rank, jobs are ordered by highest annual compensation.

```bash
# Automatically loads rankings.csv if present in the workspace:
python3 job_alert_filter.py -i emails/

# Explicitly specify a custom rankings CSV file:
python3 job_alert_filter.py -i emails/ --rankings my_rankings.csv

# Disable rankings and sort purely by compensation:
python3 job_alert_filter.py -i emails/ --no-rankings
```

---

## 📊 Presentation & Output Formats

### 1. Formatted Terminal Table (Default)
```bash
python3 job_alert_filter.py -i alerts.txt
```
```
┌──────┬───────────────────────────────┬─────────────────────┬──────────┬───────────┬───────────────┬────────────────────────┬────────────────────────────┐
│ #    │ Job Title                     │ Company             │ Co. Rank │ Pos. Rank │ Rating        │ Est. Annual CTC        │ Application URL            │
├──────┼───────────────────────────────┼─────────────────────┼──────────┼───────────┼───────────────┼────────────────────────┼────────────────────────────┤
│ 1    │ Staff Front-End Architect     │ Stripe              │ Tier 1   │ #1        │ ★ 4.3 (12.5k) │ $210,000 - $260,000/yr │ https://stripe.com/jobs... │
│ 2    │ Senior Frontend Engineer (R...│ Vercel              │ Tier 1   │ #1        │ ★ 4.5 (850)   │ $150,000 - $185,000/yr │ https://vercel.com/care... │
│ 3    │ Lead UI Engineer              │ HyperScale AI       │ Tier 2   │ #1        │ ★ 4.0 (120)   │ Unlisted               │ https://wellfound.com/j... │
│ 4    │ Frontend Web Developer (Vue...│ Creative Studio Inc │ Tier 3   │ #2        │ ★ 3.5 (45)    │ $150,000 - $190,000/yr │ https://creativestudio.... │
└──────┴───────────────────────────────┴─────────────────────┴──────────┴───────────┴───────────────┴────────────────────────┴────────────────────────────┘
```

### 2. Markdown Table
```bash
python3 job_alert_filter.py -i emails/ -f markdown
```
Ideal for pasting into Notion, GitHub issues, Obsidian, or project documentation.

### 3. CSV Export
```bash
python3 job_alert_filter.py -i emails/ -f csv > ranked_jobs.csv
```
Enables direct import into Google Sheets, Airtable, or Excel for job application tracking.

### 4. JSON Output
```bash
python3 job_alert_filter.py -i emails/ -f json
```
Structured output for downstream automation, webhooks, or cron pipelines.

---

## 🔍 Advanced Filtering Flags

```bash
# Filter only jobs offering at least $175,000 / year (or equivalent):
python3 job_alert_filter.py -i emails/ --min-salary 175000

# Add custom frontend keywords (e.g. Svelte, WebGL):
python3 job_alert_filter.py -i emails/ --include svelte webgl

# Exclude specific keywords (e.g. WordPress, QA, Junior):
python3 job_alert_filter.py -i emails/ --exclude wordpress junior intern

# Bypass frontend filters to inspect every parsed opening:
python3 job_alert_filter.py -i emails/ --all
```

---

## 📝 Input Options

`job_alert_filter.py` accepts diverse input sources via the `-i / --input` flag:

1. **Email Directory (`emails/`)**:
   ```bash
   python3 job_alert_filter.py -i emails/
   ```
2. **Raw Text File (`alerts.txt`)**:
   ```bash
   python3 job_alert_filter.py -i alerts.txt
   ```
   Paste one or more emails separated by divider lines (`---` or `===`) or standard headers (`From:`, `Subject:`).
3. **Piped Input (`stdin`)**:
   ```bash
   cat alerts.txt | python3 job_alert_filter.py -i -
   ```

---

## 🧪 Testing

Run the comprehensive unit test suite:
```bash
python3 -m unittest test_job_alert_filter.py
```
Tests cover:
- Salary normalization (USD, hourly, monthly, ranges, INR LPA formats)
- Role filtering, inclusion, and exclusion rules
- Multi-job digest card splitting (LinkedIn, Naukri)
- Company and position preference matching & sorting order
- CLI output formatting (Table, Markdown, JSON, CSV)
