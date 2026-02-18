"""
Configuration management for Job Search Tool.
Loads settings from environment variables / .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / os.getenv("DATA_DIR", "data")
CSV_PATH = DATA_DIR / "jobs.csv"

# Error log file (WARNING and ERROR from all loggers are appended here)
LOG_DIR = BASE_DIR / os.getenv("LOG_DIR", "logs")
ERROR_LOG_FILE = LOG_DIR / os.getenv("ERROR_LOG_FILE", "error_log.txt")
# Full LLM response log – every raw Ollama response is appended here for debugging
LLM_LOG_FILE = LOG_DIR / os.getenv("LLM_LOG_FILE", "llm_responses.log")
# Full LLM request log – the exact prompt sent to Ollama (system + user messages)
LLM_REQUEST_LOG_FILE = LOG_DIR / os.getenv("LLM_REQUEST_LOG_FILE", "llm_requests.log")

# Ensure data and log directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Flask
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# ── MySQL Database ─────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "job_search")

# ── API Keys (optional – sources that need them will be skipped if empty) ──
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
REED_API_KEY = os.getenv("REED_API_KEY", "")
USAJOBS_API_KEY = os.getenv("USAJOBS_API_KEY", "")
USAJOBS_EMAIL = os.getenv("USAJOBS_EMAIL", "")
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
FINDWORK_API_KEY = os.getenv("FINDWORK_API_KEY", "")
# CareerJet: register at https://www.careerjet.com/partners/api for free affid
CAREERJET_AFFID = os.getenv("CAREERJET_AFFID", "")
# JobData API: https://jobdataapi.com/docs/ – optional; without key, ~10 requests/hour (testing)
JOBDATA_API_KEY = os.getenv("JOBDATA_API_KEY", "")
# JobData: filter by country (ISO 3166-1 alpha-2). Comma-separated, e.g. US,GB for US + UK. Empty = all countries.
JOBDATA_COUNTRIES_RAW = os.getenv("JOBDATA_COUNTRIES", "US,GB")
JOBDATA_COUNTRIES = [c.strip().upper() for c in JOBDATA_COUNTRIES_RAW.split(",") if c.strip()]
# Optional: comma-separated Greenhouse board tokens (defaults to stripe, gitlab, github, etc.)
GREENHOUSE_BOARD_TOKENS = os.getenv("GREENHOUSE_BOARD_TOKENS", "")  # e.g. "stripe,gitlab,github"
# Optional: comma-separated Lever board slugs (defaults to netflix, atlassian, shopify, etc.)
LEVER_BOARD_TOKENS = os.getenv("LEVER_BOARD_TOKENS", "")  # e.g. "netflix,atlassian,shopify"
# Optional: comma-separated Ashby board names (defaults to Anthropic, Linear, Ramp, etc.)
ASHBY_BOARD_TOKENS = os.getenv("ASHBY_BOARD_TOKENS", "")  # e.g. "Anthropic,Linear,Ramp"
# Optional: comma-separated Workable account subdomains (defaults to toggl, hotjar, etc.)
WORKABLE_BOARD_TOKENS = os.getenv("WORKABLE_BOARD_TOKENS", "")  # e.g. "toggl,hotjar,wise"

# JobSpy / LinkedIn: countries to search (Indeed/Glassdoor are country-specific). Comma-separated.
# Examples: "USA" (default), "USA,UK,Canada", or "USA,UK,Canada,Australia,Germany,France,India"
JOBSPY_COUNTRIES_RAW = os.getenv("JOBSPY_COUNTRIES", "USA")
JOBSPY_COUNTRIES = [c.strip() for c in JOBSPY_COUNTRIES_RAW.split(",") if c.strip()]

# JobSpy: which sites to scrape. Comma-separated.
# Options: indeed, linkedin, glassdoor, zip_recruiter, google, bayt, naukri, bdjobs
# zip_recruiter is blocked in the EU (GDPR). If you see 403 geoblocked-gdpr, omit zip_recruiter.
# google often returns 429 (rate limit). Default excludes google; add it in .env if you want it.
JOBSPY_SITES_RAW = os.getenv("JOBSPY_SITES", "indeed,linkedin,glassdoor,zip_recruiter,bayt,naukri,bdjobs")
JOBSPY_SITES = [s.strip().lower() for s in JOBSPY_SITES_RAW.split(",") if s.strip()]
if not JOBSPY_SITES:
    JOBSPY_SITES = ["indeed", "linkedin", "glassdoor", "zip_recruiter", "bayt", "naukri", "bdjobs"]

# ── Ollama (local LLM) ─────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Open WebUI (optional unified LLM gateway) ──────────────────
# Open WebUI proxies local Ollama models AND cloud providers (Gemini, etc.)
# via a single OpenAI-compatible API. Set this URL if Open WebUI is running.
# API key: create one at Settings → Account → API Keys inside Open WebUI.
OPEN_WEBUI_BASE_URL = os.getenv("OPEN_WEBUI_BASE_URL", "http://localhost:8080")
OPEN_WEBUI_API_KEY  = os.getenv("OPEN_WEBUI_API_KEY",  "")

# ── Cloud / paid LLM providers (all optional) ──────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY",    "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_AI_API_KEY = os.getenv("GOOGLE_AI_API_KEY", "")

# Curated list of cloud models shown in the prompt-editor dropdown.
# provider must match a key handled in _call_model() in app.py.
CLOUD_MODELS: list[dict] = [
    # ── OpenAI ──────────────────────────────────────────────────
    {"id": "gpt-4o",          "provider": "openai",    "label": "GPT-4o"},
    {"id": "gpt-4o-mini",     "provider": "openai",    "label": "GPT-4o mini"},
    {"id": "o1",              "provider": "openai",    "label": "o1 (reasoning)"},
    {"id": "o3-mini",         "provider": "openai",    "label": "o3-mini (reasoning)"},
    # ── Anthropic ───────────────────────────────────────────────
    {"id": "claude-3-5-sonnet-20241022", "provider": "anthropic", "label": "Claude 3.5 Sonnet"},
    {"id": "claude-3-5-haiku-20241022",  "provider": "anthropic", "label": "Claude 3.5 Haiku"},
    {"id": "claude-3-opus-20240229",     "provider": "anthropic", "label": "Claude 3 Opus"},
    # ── Google ──────────────────────────────────────────────────
    {"id": "gemini-2.0-flash",   "provider": "google", "label": "Gemini 2.0 Flash"},
    {"id": "gemini-1.5-pro",     "provider": "google", "label": "Gemini 1.5 Pro"},
    {"id": "gemini-1.5-flash",   "provider": "google", "label": "Gemini 1.5 Flash"},
]

# Search defaults (max jobs per source per search; UI can request up to 1000)
MAX_RESULTS_PER_SOURCE = int(os.getenv("MAX_RESULTS_PER_SOURCE", "1000"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
RATE_LIMIT_DELAY = float(os.getenv("RATE_LIMIT_DELAY", "1.0"))
# JobSpy: delay in seconds between each scrape call (keyword/country) to reduce 429/CAPTCHA from Google
JOBSPY_DELAY_BETWEEN_REQUESTS = float(os.getenv("JOBSPY_DELAY_BETWEEN_REQUESTS", "8.0"))
# LinkedIn (Direct): delay in seconds between pagination requests (default 5) to avoid blocks
LINKEDIN_DIRECT_DELAY = float(os.getenv("LINKEDIN_DIRECT_DELAY", "5.0"))
# LinkedIn (Direct): locations to search when user doesn't specify one. Comma-separated (e.g. United States, United Kingdom).
LINKEDIN_DIRECT_LOCATIONS_RAW = os.getenv("LINKEDIN_DIRECT_LOCATIONS", "United States,United Kingdom")
LINKEDIN_DIRECT_LOCATIONS = [s.strip() for s in LINKEDIN_DIRECT_LOCATIONS_RAW.split(",") if s.strip()] or ["United States"]
# LinkedIn (Direct) – optional browser mode: use Playwright to open the real LinkedIn jobs page (JS-rendered).
# Lets you log in once in a persistent profile; then we open the page, scrape, and close. Requires: pip install playwright && playwright install chromium
LINKEDIN_DIRECT_USE_BROWSER = os.getenv("LINKEDIN_DIRECT_USE_BROWSER", "false").strip().lower() in ("true", "1", "yes")
LINKEDIN_DIRECT_BROWSER_HEADED = os.getenv("LINKEDIN_DIRECT_BROWSER_HEADED", "false").strip().lower() in ("true", "1", "yes")  # show browser window (e.g. to log in)
# Persistent browser profile path (so you stay logged in). Stored under LOG_DIR by default.
LINKEDIN_DIRECT_BROWSER_PROFILE = os.getenv("LINKEDIN_DIRECT_BROWSER_PROFILE", "") or str(LOG_DIR / "linkedin_browser_profile")
# Delay between clicking individual job cards in browser mode (seconds). Each click loads the detail panel.
LINKEDIN_DIRECT_CARD_DELAY = float(os.getenv("LINKEDIN_DIRECT_CARD_DELAY", "1.0"))

# Default job title keywords
DEFAULT_KEYWORDS = [
    "data analyst",
    "data scientist",
    "machine learning",
    "software engineer",
    "data engineer",
    "python developer",
    "AI engineer",
]

# Experience levels
EXPERIENCE_LEVELS = ["Entry", "Mid", "Senior", "Lead", "Executive"]

# Job types
JOB_TYPES = ["Full-time", "Part-time", "Contract", "Internship", "Freelance"]

# Remote options
REMOTE_OPTIONS = ["Any", "Remote", "On-site", "Hybrid"]
