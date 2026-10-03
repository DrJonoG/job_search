# job_search

I'm currently working full time and job hunting, consequently, my time is valuable and limited. I'm sick of checking the same 10 sites every day, half of them showing me the same listings and the aggregator tools that already exist, want you to pay just to see the results. So I built this instead. Completely free and runs on your local machine

**job_search** pulls from **30+** sources at once: Google Jobs, Indeed, LinkedIn, Glassdoor, UK boards, remote boards, ATS platforms like Greenhouse, Lever, Ashby & Workable, and more. It dumps everything into a MySQL database, deduplicates it, and gives you a clean web UI to browse, filter, favourite, and track your applications. Nothing fancy, just useful.

**job_search** can also analyse job listings against your CV using a local or cloud LLM, including the free tier of Google's Gemini API, which requires no credit card and handles a high volume of analyses at no cost. Feed it your CV, your preferences, and any other context that matters, and for each job it returns:

- **Match score** (1–10) with detailed reasoning
- **Skills you have** and **skills you're missing** for the role
- **Key responsibilities**, what you'd actually be doing day-to-day
- **Cover letter talking points** tailored to your CV and the specific job
- **Interview prep topics** based on gaps in your profile
- **Application tips**, concrete, role-specific advice
- **Red flags**, unusual requirements, vague scope, low salary for seniority, etc.
- **Company highlights**, size, type, funding, notable facts
- **Recommendation**, apply, maybe, or skip

A separate fast local résumé pre-screening layer can also rank the full job database before running deeper LLM analysis. This uses local embeddings and supports prompt-specific scores, incremental matching, Job Board score filters, and optional integration with a self-hosted Resume Matcher instance.

Models run locally via Ollama, through Open WebUI (which proxies Gemini, Claude, and others), or directly via cloud provider APIs. No data leaves your machine unless you explicitly choose a cloud model.

**Important**: **Privacy Notice**. Using external LLMs means your data is no longer private. If you do not want your CV to be used (or potentially used) for training models you should specifically use the local models via Ollama. Alternatively, retract information like your name, address and number from your CV, this will have no impact on the analysis.

**Important**: This is just a side project while I'm actively job hunting, there might be bugs, errors and anything else. Please report anything you find, and I'll do my best to fix them.

Built with Python, Flask, and MySQL. Runs locally on Windows, macOS, or Linux.

## Screenshots

| Dashboard | Job Board |
|:---------:|:---------:|
| ![Dashboard](screenshots/dashboard.png) | ![Job Board](screenshots/job-board.png) |

| Job Detail | Job Search |
|:----------:|:----------:|
| ![Job Detail](screenshots/job-detail.png) | ![Job Search](screenshots/job-search.png) |

| AI Prompt | AI Analysis |
|:----------:|:----------:|
| ![AI Prompt](screenshots/ai-prompt.png) | ![AI Analysis](screenshots/ai-analysis.png) |

---

## Features

### Multi-Source Aggregation

- **30+ job sources** in one search, free APIs, RSS feeds, web scrapers, Google Jobs, UK government boards, remote-only boards, and ATS company boards (Greenhouse, Lever, Ashby, Workable)
- Each source searches per keyword and merges results, so multiple comma-separated keywords dramatically improve coverage
- Sources run concurrently (thread pool) for fast searches across all boards. However, based on search criteria, this can still take up to an hour

### Database & Deduplication

- All jobs saved to a MySQL database with `INSERT IGNORE` on a unique MD5 hash, safe to re-run searches as many times as you like without creating duplicates
- Jobs are saved as each source finishes (crash-safe), if a search stops mid-run, you keep everything found so far
- Full-text search via a MySQL `FULLTEXT` index on title, company, description, tags, and location

### Professional Web UI

- Clean, modern design inspired by financial terminals, **IBM Plex Mono** font, green accent colour, dark/light theme toggle
- **Instant-apply filters**, no "Apply" button needed; filters update results as you change them
- **Sorting** by date posted, company, title, salary, or source
- **Pagination** with configurable page size
- **Detail modal**, click any job card to see the full description, salary, company logo, and quick-action buttons
- Job Board filter sidebar remains independently scrollable on desktop when the filter list is taller than the browser viewport

### Favourites, Applications & Not Interested

- **Heart icon** to save interesting jobs to a dedicated Favourites page
- **Applied icon** to mark jobs as applied, with optional notes (e.g. "sent CV", "phone screen scheduled")
- **Not Interested icon** (eye-slash) to dismiss jobs you don't want
- Dedicated **Favourites** and **Applied** pages for managing your pipeline
- Reviewed jobs can be hidden from the normal Job Board queue:
  - Not Interested
  - Favourites
  - Applied
- This allows the default Job Board view to act as an unreviewed-job work queue

### Notes

- **Rich text notes**, create, edit, and delete notes with a full-featured editor (bold, italic, headings, bullet/numbered lists, code blocks, links)
- Perfect for saving great application answers, interview prep, or any job-search-related thoughts
- **Full-text search** across note titles and content
- Powered by [Quill](https://quilljs.com/) rich text editor

### AI Prompts (LLM Analysis)

- **Prompt configurations** bundle your CV, a personal summary, job preferences, and extra context into a reusable config that is sent alongside a job description to a local or cloud LLM
- Create **multiple prompt configs**, e.g. one for senior data roles, one for contract positions, and switch between them freely
- Mark one as **Active** to use it as the default when running analyses and automatic résumé matching
- Each prompt stores:
  - **CV / Résumé**, full plain-text CV
  - **About Me**, a short personal summary beyond the CV
  - **What I'm Looking For**, desired role type, salary range, remote preference, location, contract type, and any hard exclusions
  - **Extra Context**, portfolio links, visa status, company-size preference, or any other instructions for the model
  - **Model**, choose from locally installed Ollama models, models available through Open WebUI (e.g. Gemini), or direct cloud APIs (OpenAI, Anthropic, Google)
- CVs can be managed manually or optionally imported from a self-hosted Resume Matcher instance
- Resume Matcher imports can remain linked for automatic synchronization or be converted back to a normal manual copy
- Analysis results are stored in `ai_analyses` and linked to both the prompt and the job, re-run with a different model or prompt without losing previous results
- Each analysis returns: keywords, key skills, job description, key responsibilities, match score (1–10) with reasoning, skills matched/missing, cover letter talking points, red flags, interview prep topics, application tips, company type/size/highlights, and a recommendation (apply / maybe / skip)
- **Bulk analysis**: select multiple jobs on the job board, favourites, or applied pages using the checkbox on each card, then click **AI Analyse selected** in the action bar that appears — all jobs are queued in one step using the active prompt (or the prompt picker if no default is set)

### Local Résumé Matching

Job Search includes a fast local pre-screening system that can rank the full job database against an AI Prompt/CV before running deeper LLM analysis.

The matcher is:

```bash
tools/resume_matcher.py
```

The embedding model is:

```text
BAAI/bge-small-en-v1.5
```

The current ranking formula is:

- **45% semantic profile fit**
- **35% target-role fit**
- **20% seniority fit**

The result is a **ranking score**. It is not an ATS score and it is not a probability of receiving an interview.

By default, the matcher uses the selected Job Search AI Prompt as the profile source.

The CV is the primary semantic matching content.

The following AI Prompt fields are also included as supplemental profile context:

- About Me
- Preferences
- Extra Context

The profile hash includes both the CV and this supplemental context. Changing any of these fields therefore creates a new scoring profile.

### Prompt-Specific Scores

Résumé scores are stored against:

- AI Prompt ID
- SHA-256 profile hash
- Job ID

This allows:

- multiple AI Prompts to coexist
- different versions of the same CV to coexist
- score history from old profile versions to remain isolated
- the Job Board to display the correct score for the selected profile

### Incremental Matching

Run against the active AI Prompt:

```bash
python3 tools/resume_matcher.py --top 30
```

Run against a specific AI Prompt:

```bash
python3 tools/resume_matcher.py --prompt-id 1 --top 30
```

Score everything eligible without displaying a top list:

```bash
python3 tools/resume_matcher.py --prompt-id 1 --top 0
```

Incremental mode skips jobs that already have a score for the exact current Prompt/profile hash:

```bash
python3 tools/resume_matcher.py \
  --prompt-id 1 \
  --incremental \
  --top 0
```

The matcher can also accept a direct résumé file instead of an AI Prompt:

```bash
python3 tools/resume_matcher.py \
  --resume /path/to/resume.pdf
```

Supported direct file types include:

- `.txt`
- `.md`
- `.pdf`
- `.docx`

### Automatic Post-Search Matching

After a normal job search completes, Job Search can automatically run incremental résumé matching using the active AI Prompt.

Only newly discovered or previously unscored jobs for the exact current profile version need to be processed.

Automatic and manual matching share the same lock so they do not run simultaneously.

### Score / Re-score Profile

The Job Board provides a **Score / Re-score Profile** action.

This performs a full score pass for the selected AI Prompt rather than incremental matching.

This is useful after manually editing:

- CV
- About Me
- Preferences
- Extra Context

### Job Board Résumé Filters

The Job Board supports:

- AI Prompt/profile selection
- profile-specific match scores
- minimum match thresholds
- score badges
- sorting/filtering by résumé match
- saved board searches that remember résumé-matcher settings

Typical minimum-score options include:

- Any
- 70+
- 75+
- 80+
- 85+

### Current Targeting Rules

The matcher currently contains configurable pre-filtering logic for technology leadership roles.

Eligible role families include:

- Chief Information Officer / CIO
- Chief Technology Officer / CTO
- Vice President technology roles
- Head of technology roles
- Senior Director technology roles
- Director technology roles

Clearly unrelated title families are rejected before embedding.

The current geography rules allow:

- explicitly remote jobs
- configured Grand Rapids-area locations

These rules live in `tools/resume_matcher.py` and can be changed for another user's target roles or geography.

### Optional Resume Matcher Integration

Job Search can optionally integrate with a separately hosted **Resume Matcher** instance.

This integration is **not required**.

Users can continue to:

1. Create an AI Prompt
2. Paste a CV directly into Job Search
3. Score jobs normally

without installing or running Resume Matcher.

Configure Resume Matcher in `.env`:

```env
RESUME_MATCHER_URL=http://localhost:3887
```

Use the hostname or IP appropriate for your environment.

Leave the setting blank:

```env
RESUME_MATCHER_URL=
```

to use Job Search entirely in manual-CV mode.

### Importing from Resume Matcher

The AI Prompts page can query the configured Resume Matcher instance and list available resumes.

Supported imported resume types include:

- master resumes
- tailored resumes

Importing a résumé populates the normal Job Search CV field.

A user may then choose to keep that prompt linked to Resume Matcher.

### Resume Link Storage

External resume linkage is deliberately stored separately from the normal AI Prompt table.

Link metadata lives in:

```text
ai_prompt_resume_links
```

while the actual locally usable CV remains cached in:

```text
ai_prompts.cv
```

This means Resume Matcher remains a soft dependency rather than becoming a requirement for Job Search.

A manual profile looks like:

```text
AI Prompt
  └── locally managed CV
```

A linked profile looks like:

```text
AI Prompt
  ├── locally cached CV
  └── optional Resume Matcher link
```

### Resume Matcher Controls

Linked prompts support:

- **Sync Resume**
- automatic synchronization before scoring
- disconnecting Resume Matcher
- **Use Manual Copy**
- retaining the locally cached CV after unlinking

Disconnecting Resume Matcher does not delete the locally stored CV.

### Automatic Resume Matcher Synchronization

Linked Resume Matcher profiles are checked automatically whenever the résumé matcher runs.

This applies to:

- **Score / Re-score Profile**
- automatic post-search incremental matching
- command-line matching using a linked AI Prompt

The synchronization flow is:

```text
matcher starts
    ↓
load AI Prompt
    ↓
is it linked to Resume Matcher?
    ↓
no ────────────────→ use local CV normally
    ↓ yes
fetch remote resume
    ↓
normalize resume content
    ↓
compare with cached CV
    ↓
unchanged ─────────→ keep existing profile version
    ↓ changed
update cached CV
update sync timestamp
    ↓
calculate new profile hash
    ↓
score eligible jobs
```

The comparison is based on normalized résumé content rather than relying only on a remote `updated_at` timestamp.

This avoids unnecessary rescoring when only external metadata changes.

When nothing changed:

```text
Resume Matcher sync: no changes
```

When content changed:

```text
Resume Matcher sync: updated linked resume (...)
```

A changed résumé automatically creates a new profile hash.

Because incremental matching looks for scores associated with the exact current profile hash, a changed linked résumé causes eligible jobs to be scored again automatically.

### Resume Matcher Failure Handling

Resume Matcher is deliberately treated as an optional service.

If a linked Resume Matcher server cannot be reached, Job Search:

1. Reports/logs the synchronization problem
2. Keeps the locally cached CV
3. Continues résumé matching
4. Does not fail the overall job-search workflow

Typical output:

```text
Resume Matcher sync: unavailable (...); using cached CV
```

Manual profiles never contact Resume Matcher.

### Saved Searches

- **Save any search configuration**, keywords, location, remote, job type, experience level, salary, sources, results cap, and posted-in-last filter
- Saved searches appear as **clickable chips** in a horizontal row above the search form, ordered by most recent
- **Click to load**, instantly populates all form fields and source selections
- **Delete** with the × button on each chip

### Saved Board Searches

- **Save any job board filter configuration**, search query, source, work type, job type, salary, posted-in-last, region, sort, order, résumé match threshold, and selected AI Prompt
- Saved board searches appear as **clickable chips** above the job board filters, ordered by most recent
- **Click to load**, instantly populates all filter fields and reloads results
- **Delete** with the × button on each chip

### Region Filter

- **Filter by region/country** on the job board, select from 20+ pre-defined regions including United Kingdom, United States, Canada, Germany, France, Netherlands, Ireland, Australia, India, and more
- Handles location data in many formats, "California, United States", "UK", "London", "united kingdom", etc.
- Includes a "Remote / Anywhere" option for jobs listed as worldwide or remote

### Rich Job Descriptions

- Job descriptions from sources **preserve original HTML formatting**, headings, bullet lists, bold text, links, tables, and paragraphs render correctly in the detail modal
- Dangerous elements (script, style, iframe, form) are sanitized on scrape
- Plain-text descriptions fall back to standard rendering

### Search Configuration

- **Keywords**, comma-separated job titles or skills
- **Location**, city, region, or country
- **Remote**, Any / Remote / On-site / Hybrid
- **Job type**, Full-time / Part-time / Contract / Internship / Freelance
- **Experience level**, Entry / Mid / Senior / Lead / Executive
- **Salary minimum**, filter out low-paying roles
- **Source selection**, choose which boards to search
- **Results cap**, limit results per source (25–1000)

### Other

- **Background search** with real-time progress tracking in the UI
- **CSV export**, download your entire job database as a `.csv` file at any time
- **Graceful error handling**, friendly setup instructions displayed when the database is unavailable
- **Extensible architecture**, add new sources via the adapter pattern (extend `BaseSource`, implement `fetch_jobs()`)

---

## Sources

### Free (no API key needed, works immediately)

| Source | Coverage |
|--------|----------|
| RemoteOK | Remote jobs worldwide |
| Arbeitnow | European & global jobs |
| The Muse | US-focused positions |
| Jobicy | Remote jobs worldwide |
| Remotive | Remote tech & non-tech jobs |
| We Work Remotely | Remote jobs (RSS by category) |
| Working Nomads | Remote jobs (API) |
| Lobsters | Lobste.rs job tag (RSS) |
| Greenhouse | Company job boards (Stripe, GitLab, GitHub, etc.) |
| **Lever** | **Company job boards (Netflix, Atlassian, Shopify, etc.), 60+ default boards** |
| **Ashby** | **Company job boards (Anthropic, Deliveroo, Ramp, etc.), 40+ default boards** |
| **Workable** | **Company job boards (Toggl, Hotjar, Wise, etc.), 50+ default boards** |
| JobsCollider | Remote jobs only |
| DevITjobs | UK developer & tech jobs |
| HN Who is hiring | Hacker News monthly hiring threads |
| Totaljobs | UK jobs |
| Remote.co | Remote jobs |
| GOV.UK Find a Job | UK official job board |
| **JobSpy** | **Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google and others** |
| **LinkedIn** | **LinkedIn jobs via JobSpy** |
| **LinkedIn (Direct)** | **In-house LinkedIn scraper** |

### Free API key required (register for free)

| Source | Registration | Coverage |
|--------|-------------|----------|
| Adzuna | [developer.adzuna.com](https://developer.adzuna.com/) | UK, US, AU & more |
| Reed | [reed.co.uk/developers](https://www.reed.co.uk/developers/jobseeker) | UK jobs |
| USAJobs | [developer.usajobs.gov](https://developer.usajobs.gov/APIRequest/Index) | US government positions |
| Jooble | [jooble.org/api](https://jooble.org/api/about) | Job aggregator |
| **Google Jobs** | [serpapi.com](https://serpapi.com/) | Google's job aggregation engine |
| Findwork | [findwork.dev](https://findwork.dev/developers/) | Developer / tech jobs |
| CareerJet | [careerjet.com/partners/api](https://www.careerjet.com/partners/api) | Job aggregator |
| **JobData** | [jobdataapi.com](https://jobdataapi.com/docs/) | Job listings with filters; optional key |

---

## Prerequisites

Before starting, make sure you have these installed:

| Software | Version | Download |
|----------|---------|----------|
| **Python** | 3.10 or newer | [python.org/downloads](https://www.python.org/downloads/) |
| **MySQL / MariaDB** | Recent version | MySQL, MariaDB, or XAMPP |
| **Git** | Any recent version | [git-scm.com/downloads](https://git-scm.com/downloads) |

---

## Installation & Setup

### Step 1: Install Python

Download Python and verify:

```bash
python --version
```

or on Linux:

```bash
python3 --version
```

### Step 2: Install MySQL

The application uses MySQL-compatible storage.

You can use:

- MySQL
- MariaDB
- XAMPP's bundled MySQL/MariaDB

Create a database using `database.sql`.

### Step 3: Create the database

From MySQL:

```bash
mysql -u root -p < database.sql
```

For an existing installation upgrading to résumé matching, run:

```bash
mysql -u root -p job_search \
  < migrations/2026-10-02_resume_matching.sql
```

The migration adds:

```text
resume_match_scores
ai_prompt_resume_links
```

without removing existing job or AI-analysis data.

### Step 4: Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/job_search.git
cd job_search
```

### Step 5: Create a virtual environment

**Windows:**

```bash
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 6: Install Python dependencies

```bash
pip install -r requirements.txt
```

The résumé matcher uses:

```text
sentence-transformers
numpy
pypdf
python-docx
```

`pypdf` and `python-docx` support optional direct PDF/DOCX résumé input.

Optional JobSpy:

```bash
pip install python-jobspy
```

Optional LinkedIn browser mode:

```bash
pip install playwright
playwright install chromium
```

### Step 7: Configure environment variables

Copy:

```bash
cp .env.example .env
```

Windows:

```bash
copy .env.example .env
```

Review:

- Database credentials
- Job-board API keys
- Ollama/Open WebUI settings
- Optional cloud AI providers
- Optional Resume Matcher URL

For Resume Matcher:

```env
RESUME_MATCHER_URL=http://localhost:3887
```

Leave blank if unused:

```env
RESUME_MATCHER_URL=
```

### Step 8: Run the application

```bash
python app.py
```

or:

```bash
python3 app.py
```

Open:

```text
http://localhost:5000
```

---

## Optional: AI Job Analysis

### Ollama

Ollama can run local models without sending CV/job data to a cloud provider.

Example:

```bash
ollama pull llama3.1
```

Default API:

```text
http://localhost:11434
```

### Open WebUI

Install:

```bash
pip install open-webui
open-webui serve
```

Default:

```text
http://localhost:8080
```

Configure `.env`:

```env
OPEN_WEBUI_BASE_URL=http://localhost:8080
OPEN_WEBUI_API_KEY=
```

Open WebUI can proxy:

- Ollama
- Gemini
- OpenAI-compatible providers
- other configured model backends

---

## Usage

1. Enter keywords
2. Set location and job filters
3. Select job sources
4. Start a search
5. Browse results on the Job Board
6. Use résumé-match scores to prioritize jobs
7. Favourite jobs worth revisiting
8. Mark applications
9. Mark jobs Not Interested
10. Run deeper AI Analysis when useful
11. Export jobs to CSV as needed

### Recommended Résumé-Matching Workflow

1. Create or select an AI Prompt
2. Add a CV manually or import one from Resume Matcher
3. Make the prompt Active if it should be used automatically
4. Click **Score / Re-score Profile**
5. Filter the Job Board by match score
6. Review high-ranking jobs
7. Run deeper AI Analysis on promising jobs
8. Normal searches automatically score newly discovered jobs
9. Linked Resume Matcher profiles synchronize before scoring

---

## Important Notes

| Topic | Detail |
|-------|--------|
| **Résumé match score** | Ranking score only. It is not an ATS score or interview probability. |
| **Resume Matcher** | Optional. Manual CV mode remains fully supported. |
| **Resume Matcher unavailable** | Linked profiles fall back to the locally cached CV. |
| **Profile changes** | Changing CV/About Me/Preferences/Extra Context creates a new profile hash. |
| **Incremental matching** | Only jobs without scores for the exact current profile version are processed. |
| **Automatic matching** | Runs after normal searches using the active AI Prompt. |
| **ZipRecruiter in the EU** | ZipRecruiter may return 403 from EU IPs. |
| **Google 429** | Google scraping can be rate limited. |
| **LinkedIn browser mode** | Requires Playwright and a saved login session. |
| **JobData API** | Without an API key, request limits are lower. |
| **JobSpy 429/CAPTCHA** | Increase the configured request delay if required. |

---

## File Structure

```text
job_search/
├── app.py
├── config.py
├── prompts.py
├── database.sql
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── migrations/
│   └── 2026-10-02_resume_matching.sql
├── tools/
│   └── resume_matcher.py
├── data/
├── logs/
├── screenshots/
├── job_scraper/
│   ├── __init__.py
│   ├── models.py
│   ├── storage.py
│   ├── manager.py
│   └── sources/
│       ├── __init__.py
│       ├── base.py
│       ├── remoteok.py
│       ├── arbeitnow.py
│       ├── themuse.py
│       ├── jobicy.py
│       ├── remotive.py
│       ├── weworkremotely.py
│       ├── jobspy_source.py
│       ├── linkedin.py
│       ├── linkedin_direct.py
│       ├── adzuna.py
│       ├── reed.py
│       ├── usajobs.py
│       ├── jooble.py
│       ├── serpapi_google.py
│       ├── findwork.py
│       ├── jobdata.py
│       ├── careerjet.py
│       ├── totaljobs.py
│       ├── remote_co.py
│       ├── govuk_findajob.py
│       ├── greenhouse.py
│       ├── lever.py
│       ├── ashby.py
│       ├── workable.py
│       ├── jobscollider.py
│       ├── devitjobs.py
│       ├── workingnomads.py
│       ├── lobsters.py
│       └── hn_hiring.py
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── jobs.html
│   ├── favourites.html
│   ├── applied.html
│   ├── notes.html
│   ├── ai_prompts.html
│   └── error.html
└── static/
    ├── css/
    │   └── style.css
    └── js/
        ├── app.js
        └── ai_analyse.js
```

---

## Database Schema

### `sources`

Reference table for configured job sources.

### `jobs`

Primary job-listing storage.

Important fields include:

| Column | Description |
|--------|-------------|
| `job_id` | Unique job identifier / deduplication key |
| `title` | Job title |
| `company` | Company |
| `location` | Location |
| `description` | Job description |
| `url` | Job URL |
| `source` | Source |
| `remote` | Remote/on-site/hybrid |
| `salary_min` | Minimum salary |
| `salary_max` | Maximum salary |
| `job_type` | Job type |
| `experience_level` | Experience level |
| `date_posted` | Source posted date |
| `date_scraped` | Local scrape date |

### `favourites`

Stores jobs marked as favourites.

### `applications`

Stores application status and optional notes.

### `not_interested`

Stores jobs dismissed by the user.

### `notes`

Stores rich-text notes.

### `saved_searches`

Stores dashboard search configurations as JSON.

### `saved_board_searches`

Stores Job Board filter configurations as JSON.

This includes résumé-match filtering and selected profile information when present.

### `ai_prompts`

Reusable AI configurations containing:

- title
- model
- CV
- About Me
- Preferences
- Extra Context
- active/default status

### `ai_analyses`

Stores structured LLM analysis output for a job/prompt combination.

### `resume_match_scores`

Stores fast local résumé-ranking results.

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGINT | Primary key |
| `prompt_id` | BIGINT | AI Prompt used for scoring |
| `resume_hash` | VARCHAR(64) | SHA-256 hash of CV + supplemental profile context |
| `job_id` | VARCHAR(255) | Job identifier |
| `match_score` | DECIMAL(6,2) | Overall ranking score |
| `semantic_score` | DECIMAL(6,2) | Semantic similarity component |
| `title_score` | DECIMAL(6,2) | Role/title component |
| `best_resume_chunk` | TEXT | Highest-relevance CV chunk |
| `model_name` | VARCHAR(255) | Embedding model |
| `analyzed_at` | DATETIME | Score timestamp |

Unique constraint:

```text
(prompt_id, resume_hash, job_id)
```

Indexes include:

```text
job_id
match_score
```

### `ai_prompt_resume_links`

Optional link between a Job Search AI Prompt and an external résumé source.

| Column | Type | Description |
|--------|------|-------------|
| `prompt_id` | BIGINT | Local AI Prompt ID |
| `source` | VARCHAR(32) | External provider, currently `resume_matcher` |
| `external_id` | VARCHAR(128) | External résumé identifier |
| `external_name` | VARCHAR(255) | External résumé display name |
| `synced_at` | DATETIME | Last successful content synchronization |
| `updated_at` | TIMESTAMP | Link record update time |

Manual CV profiles do not require a row in this table.

---

## API Endpoints

### Jobs & Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/search` | Start a background search |
| `GET` | `/api/search/<id>` | Poll search progress |
| `POST` | `/api/search/<id>/cancel` | Cancel a running search |
| `GET` | `/api/jobs` | Query jobs with filters and pagination |
| `GET` | `/api/jobs/<id>` | Get job detail |
| `POST` | `/api/jobs/statuses` | Bulk job status lookup |
| `GET` | `/api/stats` | Summary statistics |
| `GET` | `/api/sources` | Available sources |
| `GET` | `/api/export` | CSV export |

### LinkedIn Setup

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/linkedin/setup` | Start LinkedIn browser setup |
| `GET` | `/api/linkedin/setup/<id>` | Poll setup state |
| `POST` | `/api/linkedin/setup/<id>/complete` | Save session and close browser |

### Favourites

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/favourites` | List favourites |
| `POST` | `/api/favourite/<job_id>` | Add favourite |
| `DELETE` | `/api/favourite/<job_id>` | Remove favourite |

### Applications

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/applications` | List applied jobs |
| `POST` | `/api/applied/<job_id>` | Mark applied |
| `DELETE` | `/api/applied/<job_id>` | Remove applied status |
| `PUT` | `/api/applied/<job_id>/notes` | Update application notes |

### Not Interested

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/not-interested/<job_id>` | Mark not interested |
| `DELETE` | `/api/not-interested/<job_id>` | Remove not-interested status |

### Saved Searches

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/saved-searches` | List saved searches |
| `POST` | `/api/saved-searches` | Create saved search |
| `GET` | `/api/saved-searches/<id>` | Get saved search |
| `PUT` | `/api/saved-searches/<id>` | Update saved search |
| `DELETE` | `/api/saved-searches/<id>` | Delete saved search |

### Saved Board Searches

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/saved-board-searches` | List saved board searches |
| `POST` | `/api/saved-board-searches` | Create saved board search |
| `GET` | `/api/saved-board-searches/<id>` | Get saved board search |
| `PUT` | `/api/saved-board-searches/<id>` | Update saved board search |
| `DELETE` | `/api/saved-board-searches/<id>` | Delete saved board search |

### Notes

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/notes` | List/search notes |
| `POST` | `/api/notes` | Create note |
| `GET` | `/api/notes/<id>` | Get note |
| `PUT` | `/api/notes/<id>` | Update note |
| `DELETE` | `/api/notes/<id>` | Delete note |

### AI Prompts

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/ai-prompts` | List AI Prompts |
| `POST` | `/api/ai-prompts` | Create AI Prompt |
| `GET` | `/api/ai-prompts/<id>` | Get AI Prompt |
| `PUT` | `/api/ai-prompts/<id>` | Update AI Prompt |
| `DELETE` | `/api/ai-prompts/<id>` | Delete AI Prompt |
| `POST` | `/api/ai-prompts/<id>/activate` | Make prompt active |

### Résumé Matching

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/resume-match/<prompt_id>` | Fully score/re-score the selected AI Prompt against eligible jobs |

The manual score endpoint uses the same matcher and lock as automatic post-search matching.

### Resume Matcher Integration

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/resume-matcher/status` | Check configured Resume Matcher availability |
| `GET` | `/api/resume-matcher/resumes` | List resumes from Resume Matcher |
| `GET` | `/api/resume-matcher/resumes/<resume_id>` | Fetch/import a Resume Matcher resume |
| `POST` | `/api/ai-prompts/<prompt_id>/resume-link` | Link an AI Prompt to an external resume |
| `DELETE` | `/api/ai-prompts/<prompt_id>/resume-link` | Remove external resume linkage |
| `POST` | `/api/ai-prompts/<prompt_id>/sync-resume` | Explicitly synchronize a linked resume |

### AI Analysis

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/ollama/models` | List available models |
| `POST` | `/api/ai-analyse` | Run deep AI job analysis |
| `GET` | `/api/ai-analyses/<job_id>` | Return saved analyses for a job |

---

## Search Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `keywords` | string | Comma-separated job titles / keywords |
| `location` | string | City, region, or country |
| `remote` | string | Any / Remote / On-site / Hybrid |
| `job_type` | string | Full-time / Part-time / Contract / Internship / Freelance |
| `experience_level` | string | Entry / Mid / Senior / Lead / Executive |
| `salary_min` | number | Minimum annual salary |
| `sources` | list | Sources to query |
| `max_results_per_source` | number | Per-source result cap |
| `posted_in_last_days` | number | Only jobs posted within N days |

---

## Adding a New Source

1. Create a new adapter in `job_scraper/sources/`
2. Extend `BaseSource`
3. Implement `fetch_jobs()`
4. Normalize search keywords
5. Register the adapter in `job_scraper/sources/__init__.py`
6. Add configuration to `config.py` / `.env.example` if required
7. Add the source to `database.sql`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Database unavailable** | Verify MySQL is running and `.env` DB settings are correct. |
| **`pip install` fails** | Verify Python/venv setup. |
| **SentenceTransformer import failure** | Run `pip install -r requirements.txt`. |
| **PDF résumé cannot be read** | Verify `pypdf` is installed. |
| **DOCX résumé cannot be read** | Verify `python-docx` is installed. |
| **Résumé scores missing** | Run Score / Re-score Profile for the selected AI Prompt. |
| **Incremental matcher scores 0 jobs** | This can be normal if every eligible job is already scored for the exact current profile hash. |
| **Resume Matcher unavailable** | Linked profiles use the cached local CV; verify `RESUME_MATCHER_URL` and network connectivity. |
| **Resume Matcher not used** | Confirm the AI Prompt has an entry in `ai_prompt_resume_links`. |
| **Manual profile never syncs** | Expected. Manual profiles intentionally do not contact Resume Matcher. |
| **ZipRecruiter 403** | Remove it from JobSpy sources if blocked in your region. |
| **JobSpy 429/CAPTCHA** | Increase the request delay. |
| **LinkedIn Direct returns nothing** | Increase delay or use browser mode. |
| **Playwright errors** | Run `playwright install chromium`. |

---

## Tech Stack

- **Backend:** Python 3.10+, Flask
- **Database:** MySQL / MariaDB
- **Frontend:** HTML, CSS, vanilla JavaScript
- **Font:** IBM Plex Mono
- **Embedding / résumé ranking:** SentenceTransformers
- **Default résumé embedding model:** `BAAI/bge-small-en-v1.5`
- **Document parsing:** pypdf, python-docx
- **LLMs:** Ollama, Open WebUI, optional cloud providers
- **Scraping:** Requests, BeautifulSoup, Feedparser, python-jobspy, Playwright
- **Optional résumé source:** self-hosted Resume Matcher

---

## License

This project is provided as-is for personal use. See [LICENSE](LICENSE) for details.
