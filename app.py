"""
Job Search Tool – Flask web application.

Routes:
  /                     Dashboard & search form
  /jobs                 Browse saved jobs
  /favourites           Browse favourite jobs
  /applied              Browse applied jobs
  /notes                Browse & manage notes
  /ai-prompts           Manage AI analysis prompt configurations
  /api/search           POST – start a new search
  /api/search/<id>      GET  – poll search progress
  /api/jobs             GET  – query saved jobs (JSON)
  /api/jobs/<id>        GET  – single job detail (JSON)
  /api/jobs/statuses    POST – bulk favourite/applied status
  /api/stats            GET  – summary statistics
  /api/sources          GET  – available sources
  /api/export           GET  – download CSV
  /api/favourite/<id>   POST/DELETE – toggle favourite
  /api/applied/<id>     POST/DELETE – toggle applied
  /api/not-interested/<id> POST/DELETE – toggle not interested
  /api/regions          GET  – available region labels
  /api/notes            GET/POST – list / create notes
  /api/notes/<id>       GET/PUT/DELETE – read / update / delete a note
  /api/saved-board-searches     GET/POST – list / create saved board searches
  /api/saved-board-searches/<id> GET/PUT/DELETE – read / update / delete
  /api/ai-prompts               GET/POST – list / create AI prompt configs
  /api/ai-prompts/<id>          GET/PUT/DELETE – read / update / delete
  /api/ai-prompts/<id>/activate POST – set as active prompt
  /api/ollama/models            GET  – list installed Ollama models
  /api/ai-analyse               POST – run Ollama analysis on a job (blocking)
  /api/ai-analyses/<job_id>     GET  – all analyses for a job
"""

from __future__ import annotations

import logging
import math
import time

import re
import urllib.request
import urllib.error
import json as _json

from flask import Flask, render_template, request, jsonify, Response, g

import config
import prompts as _prompts
from job_scraper.storage import JobStorage, DatabaseUnavailable
from job_scraper.manager import SearchManager
from job_scraper.sources import ALL_SOURCES, FREE_SOURCES, API_KEY_SOURCES

# ╭──────────────────────────────────────────────────────────────╮
# │  AI Analysis – system prompt, helpers                        │
# │  Prompt text lives in prompts.py — edit it there.           │
# ╰──────────────────────────────────────────────────────────────╯

ANALYSIS_SYSTEM_PROMPT  = _prompts.ANALYSIS_SYSTEM_PROMPT
_ANALYSIS_REQUIRED      = _prompts.ANALYSIS_REQUIRED_FIELDS
_VALID_RECOMMENDATIONS  = _prompts.VALID_RECOMMENDATIONS


def _build_analysis_user_message(prompt_config: dict, job: dict) -> str:
    """Compose the user-turn message combining candidate context with job data."""
    cv            = (prompt_config.get("cv")            or "").strip() or "(not provided)"
    about_me      = (prompt_config.get("about_me")      or "").strip() or "(not provided)"
    preferences   = (prompt_config.get("preferences")   or "").strip() or "(not provided)"
    extra_context = (prompt_config.get("extra_context") or "").strip() or "(not provided)"

    salary_parts: list[str] = []
    if job.get("salary_min"):
        salary_parts.append(str(job["salary_min"]))
    if job.get("salary_max"):
        salary_parts.append(str(job["salary_max"]))
    salary_str = " – ".join(salary_parts)
    if job.get("salary_currency") and salary_str:
        salary_str = f"{job['salary_currency']} {salary_str}"
    salary_str = salary_str or "Not specified"

    return (
        f"CANDIDATE CV:\n{cv}\n\n"
        f"ABOUT THE CANDIDATE:\n{about_me}\n\n"
        f"WHAT THE CANDIDATE IS LOOKING FOR:\n{preferences}\n\n"
        f"ADDITIONAL CONTEXT:\n{extra_context}\n\n"
        f"---\n\n"
        f"JOB LISTING:\n"
        f"Title:    {job.get('title', '')}\n"
        f"Company:  {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Remote:   {job.get('remote', 'Not specified')}\n"
        f"Job Type: {job.get('job_type', 'Not specified')}\n"
        f"Salary:   {salary_str}\n\n"
        f"Description:\n{job.get('description', '')}"
    )


def _extract_json(text: str) -> dict:
    """
    Attempt to extract a JSON object from the LLM response using three strategies:
    1. Direct parse (model obeyed instructions).
    2. Strip markdown code fences (```json ... ``` or ``` ... ```).
    3. Find the outermost { ... } substring.
    Raises ValueError if all strategies fail.
    """
    text = text.strip()

    # Strategy 1: direct
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        pass

    # Strategy 2: markdown fences
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence:
        try:
            return _json.loads(fence.group(1))
        except _json.JSONDecodeError:
            pass

    # Strategy 3: outermost braces
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return _json.loads(text[start : end + 1])
        except _json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON object found in LLM response")


def _validate_analysis(data: dict) -> list[str]:
    """
    Validate a parsed analysis dict against the required schema.
    Returns a list of human-readable error strings (empty = valid).
    Normalises match_score to int and recommendation to lowercase in-place.
    """
    errors: list[str] = []

    for field, expected in _ANALYSIS_REQUIRED.items():
        if field not in data:
            errors.append(f"missing field '{field}'")
            continue

        val = data[field]

        if expected is str:
            if not isinstance(val, str) or not val.strip():
                errors.append(f"'{field}' must be a non-empty string (got {type(val).__name__})")
        elif expected is list:
            if not isinstance(val, list):
                errors.append(f"'{field}' must be an array (got {type(val).__name__})")
        elif field == "match_score":
            try:
                score = int(val)
                if not 1 <= score <= 10:
                    errors.append(f"'match_score' must be 1-10 (got {val})")
                else:
                    data["match_score"] = score
            except (TypeError, ValueError):
                errors.append(f"'match_score' must be a number (got {type(val).__name__}: {val!r})")

    if "recommendation" in data:
        rec = str(data.get("recommendation", "")).strip().lower()
        if rec not in _VALID_RECOMMENDATIONS:
            errors.append(
                f"'recommendation' must be one of {sorted(_VALID_RECOMMENDATIONS)!r} (got {data['recommendation']!r})"
            )
        else:
            data["recommendation"] = rec

    return errors


def _log_llm_response(
    job_id: str,
    prompt_id: int,
    prompt_title: str,
    model: str,
    raw_response: str,
) -> None:
    """
    Append the complete raw LLM response to llm_responses.log.
    Called unconditionally after every Ollama call so engineers can
    inspect exactly what the model returned, regardless of whether
    JSON extraction / validation succeeds.
    """
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep  = "=" * 80
    dash = "-" * 80
    entry = (
        f"\n{sep}\n"
        f"Timestamp : {ts}\n"
        f"Job       : {job_id}\n"
        f"Prompt    : #{prompt_id} — {prompt_title}\n"
        f"Model     : {model}\n"
        f"{dash}\n"
        f"{raw_response}\n"
        f"{sep}\n"
    )
    try:
        with open(config.LLM_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError as exc:
        logger.warning("Could not write LLM log: %s", exc)


def _log_llm_request(
    job_id: str,
    prompt_id: int,
    prompt_title: str,
    model: str,
    messages: list[dict],
) -> None:
    """
    Append the exact prompt sent to Ollama to llm_requests.log.
    Logs both the system message and the user message so the full context
    that the model receives can be inspected for debugging / prompt iteration.
    """
    import datetime
    ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep  = "=" * 80
    dash = "-" * 80
    parts = [
        f"\n{sep}",
        f"Timestamp : {ts}",
        f"Job       : {job_id}",
        f"Prompt    : #{prompt_id} — {prompt_title}",
        f"Model     : {model}",
    ]
    for msg in messages:
        role    = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        parts.append(dash)
        parts.append(f"[{role}]")
        parts.append(content)
    parts.append(sep)
    entry = "\n".join(parts) + "\n"
    try:
        with open(config.LLM_REQUEST_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError as exc:
        logger.warning("Could not write LLM request log: %s", exc)


def _call_ollama(model: str, messages: list[dict], timeout: int = 300) -> str:
    """
    POST to Ollama's /api/chat endpoint (non-streaming).
    Returns the assistant message content string.
    Raises RuntimeError on any failure (connection, HTTP error, bad response shape).
    """
    url     = config.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    payload = _json.dumps({
        "model":    model,
        "stream":   False,
        "messages": messages,
        "options":  {"temperature": 0.1},
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = _json.loads(exc.read().decode("utf-8"))
            raise RuntimeError(f"Ollama error: {err_body.get('error', str(exc))}") from exc
        except (ValueError, AttributeError):
            raise RuntimeError(f"Ollama HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama unreachable ({config.OLLAMA_BASE_URL}): {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Ollama timed out after {timeout}s") from exc

    if "error" in body:
        raise RuntimeError(f"Ollama error: {body['error']}")
    try:
        return body["message"]["content"]
    except KeyError as exc:
        raise RuntimeError(f"Unexpected Ollama response shape: missing key {exc}") from exc


def _call_openai(model: str, messages: list[dict], timeout: int = 300) -> str:
    """Call OpenAI chat-completions API. Raises RuntimeError on any failure."""
    api_key = config.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in your environment / .env file")
    url     = "https://api.openai.com/v1/chat/completions"
    payload = _json.dumps({
        "model":       model,
        "messages":    messages,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = _json.loads(exc.read().decode("utf-8"))
            raise RuntimeError(f"OpenAI error: {err_body.get('error', {}).get('message', str(exc))}") from exc
        except (ValueError, AttributeError):
            raise RuntimeError(f"OpenAI HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI unreachable: {exc.reason}") from exc
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected OpenAI response shape: {exc}") from exc


def _call_anthropic(model: str, messages: list[dict], timeout: int = 300) -> str:
    """Call Anthropic messages API. Raises RuntimeError on any failure."""
    api_key = config.ANTHROPIC_API_KEY
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in your environment / .env file")
    # Anthropic separates the system message from the messages array
    system_content = ""
    user_messages: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_content = msg.get("content", "")
        else:
            user_messages.append(msg)
    url     = "https://api.anthropic.com/v1/messages"
    payload = _json.dumps({
        "model":      model,
        "max_tokens": 4096,
        "system":     system_content,
        "messages":   user_messages,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = _json.loads(exc.read().decode("utf-8"))
            raise RuntimeError(f"Anthropic error: {err_body.get('error', {}).get('message', str(exc))}") from exc
        except (ValueError, AttributeError):
            raise RuntimeError(f"Anthropic HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Anthropic unreachable: {exc.reason}") from exc
    try:
        return body["content"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Anthropic response shape: {exc}") from exc


def _call_google(model: str, messages: list[dict], timeout: int = 300) -> str:
    """Call Google Gemini generateContent API. Raises RuntimeError on any failure."""
    api_key = config.GOOGLE_AI_API_KEY
    if not api_key:
        raise RuntimeError("GOOGLE_AI_API_KEY is not set in your environment / .env file")
    # Extract system instruction and convert messages to Gemini format
    system_text = ""
    contents: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "system":
            system_text = text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    body_dict: dict = {
        "contents":          contents,
        "generationConfig":  {"temperature": 0.1},
    }
    if system_text:
        body_dict["systemInstruction"] = {"parts": [{"text": system_text}]}
    url     = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = _json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = _json.loads(exc.read().decode("utf-8"))
            raise RuntimeError(f"Google AI error: {err_body.get('error', {}).get('message', str(exc))}") from exc
        except (ValueError, AttributeError):
            raise RuntimeError(f"Google AI HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Google AI unreachable: {exc.reason}") from exc
    try:
        return body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Google AI response shape: {exc}") from exc


def _owui_normalise_messages(messages: list[dict]) -> list[dict]:
    """
    Some models served via Open WebUI (e.g. Gemma via Google AI) do not accept
    a 'system' role message and return INVALID_ARGUMENT if one is present.
    To support all models transparently, prepend the system content to the first
    user message so every model receives the full context regardless of whether
    it supports system instructions.
    """
    system_parts: list[str] = []
    user_messages: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_parts.append(msg.get("content", ""))
        else:
            user_messages.append(dict(msg))  # shallow copy

    if not system_parts or not user_messages:
        return messages  # nothing to merge

    system_text = "\n\n".join(system_parts)
    first = user_messages[0]
    if first.get("role") == "user":
        first["content"] = f"{system_text}\n\n{first.get('content', '')}"

    return user_messages


def _call_open_webui(model: str, messages: list[dict], timeout: int = 300) -> str:
    """
    Call a model through Open WebUI's OpenAI-compatible /api/chat/completions
    endpoint. Open WebUI proxies both local Ollama models and cloud providers
    (Gemini, Claude, etc.) behind a single API, so no separate provider keys
    are needed as long as they are configured inside Open WebUI.

    System messages are merged into the first user message so models that do
    not support the system role (e.g. Gemma via Google AI) work correctly.
    Raises RuntimeError on any failure.
    """
    base    = config.OPEN_WEBUI_BASE_URL.rstrip("/")
    api_key = config.OPEN_WEBUI_API_KEY
    url     = f"{base}/api/chat/completions"
    payload = _json.dumps({
        "model":       model,
        "messages":    _owui_normalise_messages(messages),
        "stream":      False,
        "temperature": 0.1,
    }).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8")
            err_body = _json.loads(raw)
            msg = (
                err_body.get("detail")
                or err_body.get("message")
                or err_body.get("error", {}).get("message")
                or str(exc)
            )
            raise RuntimeError(f"Open WebUI error ({exc.code}): {msg}") from exc
        except (ValueError, AttributeError):
            raise RuntimeError(
                f"Open WebUI HTTP {exc.code}: {exc.reason}"
                + (f" — {raw[:200]}" if raw else "")
            ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Open WebUI unreachable ({base}): {exc.reason}") from exc
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Open WebUI response shape: {exc}") from exc


# ── Model prefix → direct provider (fallback when Open WebUI not used) ──
_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("gpt-",     "openai"),
    ("o1",       "openai"),
    ("o3",       "openai"),
    ("chatgpt-", "openai"),
    ("claude-",  "anthropic"),
    ("gemini-",  "google"),
]

# Sentinel prefix stored in the DB when a model was chosen from Open WebUI.
# Stripped before sending to the API.
_OWUI_PREFIX = "owui:"


def _call_model(model: str, messages: list[dict], timeout: int = 300) -> str:
    """
    Route to the correct LLM provider.

    If the model ID starts with the 'owui:' sentinel it was sourced from Open
    WebUI and is called through its OpenAI-compatible API.  Otherwise fall back
    to direct provider routing (OpenAI / Anthropic / Google) or local Ollama.
    """
    if model.startswith(_OWUI_PREFIX):
        real_model = model[len(_OWUI_PREFIX):]
        return _call_open_webui(real_model, messages, timeout)

    model_lower = model.lower()
    for prefix, provider in _PROVIDER_PREFIXES:
        if model_lower.startswith(prefix):
            if provider == "openai":
                return _call_openai(model, messages, timeout)
            if provider == "anthropic":
                return _call_anthropic(model, messages, timeout)
            if provider == "google":
                return _call_google(model, messages, timeout)
    return _call_ollama(model, messages, timeout)


# ── Logging ────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# Silence werkzeug's per-request spam (GET /api/search/... every second)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Also write WARNING and ERROR to error_log file
try:
    file_handler = logging.FileHandler(config.ERROR_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(file_handler)
except OSError as e:
    logger.warning("Could not create error log file %s: %s", config.ERROR_LOG_FILE, e)

# ── App setup ──────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = config.SECRET_KEY

storage = JobStorage()
manager = SearchManager(storage)


# ── Request logging (replaces werkzeug spam) ───────────────────

# Endpoints to never log (high-frequency polling / bulk status checks)
_SILENT_PREFIXES = ("/api/search/", "/api/stats", "/api/jobs/statuses", "/api/sources")

@app.before_request
def _before_request():
    g.req_start = time.time()

@app.after_request
def _after_request(response):
    # Skip noisy polling endpoints
    path = request.path
    if any(path.startswith(p) for p in _SILENT_PREFIXES) and request.method == "GET":
        return response
    elapsed = round((time.time() - getattr(g, "req_start", time.time())) * 1000)
    status = response.status_code
    method = request.method
    if status >= 400:
        logger.warning("%s %s → %d (%dms)", method, path, status, elapsed)
    else:
        logger.info("%s %s → %d (%dms)", method, path, status, elapsed)
    return response


# ── Database error handler ─────────────────────────────────────

@app.errorhandler(DatabaseUnavailable)
def handle_db_error(exc):
    """Show a friendly error page when the database is unreachable."""
    logger.error("Database unavailable: %s", exc)
    return render_template("error.html", error_detail=str(exc)), 503


def _check_db():
    """Raise DatabaseUnavailable early on page loads so the error handler fires."""
    from job_scraper.storage import check_db_connection
    ok, msg = check_db_connection()
    if not ok:
        raise DatabaseUnavailable(msg)


# ╭──────────────────────────────────────────────────────────────╮
# │  Page routes                                                 │
# ╰──────────────────────────────────────────────────────────────╯

@app.route("/")
def index():
    """Dashboard – search form + stats."""
    _check_db()
    stats = storage.get_stats()
    sources_info = _get_sources_info()
    return render_template(
        "index.html",
        stats=stats,
        sources=sources_info,
        default_keywords=config.DEFAULT_KEYWORDS,
        experience_levels=config.EXPERIENCE_LEVELS,
        job_types=config.JOB_TYPES,
        remote_options=config.REMOTE_OPTIONS,
    )


@app.route("/jobs")
def jobs_page():
    """Browse / search saved jobs."""
    _check_db()
    stats = storage.get_stats()
    sources_list = storage.get_sources()
    return render_template(
        "jobs.html",
        stats=stats,
        sources=sources_list,
        job_types=config.JOB_TYPES,
        remote_options=config.REMOTE_OPTIONS,
    )


@app.route("/favourites")
def favourites_page():
    """Browse favourite jobs."""
    _check_db()
    stats = storage.get_stats()
    return render_template("favourites.html", stats=stats)


@app.route("/applied")
def applied_page():
    """Browse applied jobs."""
    _check_db()
    stats = storage.get_stats()
    return render_template("applied.html", stats=stats)


@app.route("/notes")
def notes_page():
    """Browse & manage notes."""
    _check_db()
    stats = storage.get_stats()
    return render_template("notes.html", stats=stats)


@app.route("/ai-prompts")
def ai_prompts_page():
    """Manage AI analysis prompt configurations."""
    _check_db()
    stats = storage.get_stats()
    return render_template("ai_prompts.html", stats=stats)


@app.route("/ai-analysis")
def ai_analysis_page():
    """Browse all AI analysis results."""
    _check_db()
    prompts = storage.get_ai_prompts()
    return render_template("ai_analysis.html", prompts=prompts)


# ╭──────────────────────────────────────────────────────────────╮
# │  API routes                                                  │
# ╰──────────────────────────────────────────────────────────────╯

@app.route("/api/search", methods=["POST"])
def api_start_search():
    """Start a background search across selected sources."""
    data = request.get_json(silent=True) or {}

    keywords_raw = data.get("keywords", "")
    if isinstance(keywords_raw, str):
        keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    else:
        keywords = list(keywords_raw)

    # Empty keywords = "search all" (use a broad fallback term for API sources)
    if not keywords:
        keywords = ["job"]

    location = data.get("location", "")
    remote = data.get("remote", "Any")
    job_type = data.get("job_type", "")
    salary_min = None
    if data.get("salary_min"):
        try:
            salary_min = float(data["salary_min"])
        except ValueError:
            pass
    experience_level = data.get("experience_level", "")
    sources = data.get("sources", None)
    max_results = int(data.get("max_results_per_source", config.MAX_RESULTS_PER_SOURCE))
    posted_in_last_days = data.get("posted_in_last_days")
    if posted_in_last_days is not None:
        try:
            posted_in_last_days = int(posted_in_last_days)
            if posted_in_last_days <= 0:
                posted_in_last_days = None
        except (ValueError, TypeError):
            posted_in_last_days = None

    task_id = manager.start_search(
        keywords=keywords,
        location=location,
        remote=remote,
        job_type=job_type,
        salary_min=salary_min,
        experience_level=experience_level,
        sources=sources,
        max_results_per_source=max_results,
        posted_in_last_days=posted_in_last_days,
    )

    src_count = len(sources) if sources else "all"
    logger.info(
        "Search started [%s]  keywords=%s  location=%s  remote=%s  sources=%s  max=%d",
        task_id, keywords, location or "(any)", remote, src_count, max_results,
    )

    return jsonify({"task_id": task_id, "status": "started"})


@app.route("/api/search/<task_id>")
def api_search_status(task_id):
    """Poll search progress."""
    task = manager.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task.to_dict())


@app.route("/api/search/<task_id>/cancel", methods=["POST"])
def api_search_cancel(task_id):
    """Request cancellation of a running search."""
    if not manager.cancel_search(task_id):
        return jsonify({"error": "Task not found or not running"}), 400
    return jsonify({"status": "cancellation requested"})


@app.route("/api/jobs")
def api_jobs():
    """Query saved jobs with filters + pagination."""
    query = request.args.get("q", "")
    source = request.args.get("source", "")
    remote = request.args.get("remote", "")
    job_type = request.args.get("job_type", "")
    salary_min = request.args.get("salary_min", type=float, default=None)
    posted_in_last_days = request.args.get("posted_in_last_days", type=int, default=None)
    if posted_in_last_days is not None and posted_in_last_days <= 0:
        posted_in_last_days = None
    sort_by = request.args.get("sort_by", "date_posted")
    order = request.args.get("order", "desc")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    include_not_interested = request.args.get("include_not_interested", "0")
    exclude_ni = include_not_interested not in ("1", "true", "yes")
    region = request.args.get("region", "")

    all_jobs = storage.search(
        query=query,
        source=source,
        remote=remote,
        job_type=job_type,
        salary_min=salary_min,
        posted_in_last_days=posted_in_last_days,
        sort_by=sort_by,
        ascending=(order == "asc"),
        exclude_not_interested=exclude_ni,
        region=region,
    )

    total = len(all_jobs)
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page

    jobs = all_jobs[start:end]

    return jsonify({
        "jobs": jobs,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        },
    })


@app.route("/api/jobs/<job_id>")
def api_job_detail(job_id):
    """Get a single job by ID (includes favourite/applied status)."""
    job = storage.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/jobs/statuses", methods=["POST"])
def api_job_statuses():
    """Bulk check favourite/applied status for a list of job_ids."""
    data = request.get_json(silent=True) or {}
    job_ids = data.get("job_ids", [])
    statuses = storage.get_job_statuses(job_ids)
    return jsonify(statuses)


@app.route("/api/stats")
def api_stats():
    """Summary statistics."""
    return jsonify(storage.get_stats())


@app.route("/api/sources")
def api_sources():
    """Available sources with status."""
    return jsonify(_get_sources_info())


@app.route("/api/export")
def api_export():
    """Download all jobs as a CSV file."""
    csv_data = storage.export_csv_string()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobs_export.csv"},
    )


# ── Favourites API ─────────────────────────────────────────────

@app.route("/api/favourite/<job_id>", methods=["POST"])
def api_add_favourite(job_id):
    """Add a job to favourites."""
    added = storage.add_favourite(job_id)
    if added:
        logger.info("Favourite added: %s", job_id[:12])
    return jsonify({"status": "added" if added else "already_exists", "job_id": job_id})


@app.route("/api/favourite/<job_id>", methods=["DELETE"])
def api_remove_favourite(job_id):
    """Remove a job from favourites."""
    removed = storage.remove_favourite(job_id)
    if removed:
        logger.info("Favourite removed: %s", job_id[:12])
    return jsonify({"status": "removed" if removed else "not_found", "job_id": job_id})


@app.route("/api/favourites")
def api_favourites():
    """Get all favourite jobs."""
    sort_by = request.args.get("sort_by", "created_at")
    order = request.args.get("order", "desc")
    jobs = storage.get_favourites(sort_by=sort_by, ascending=(order == "asc"))
    return jsonify({"jobs": jobs, "total": len(jobs)})


# ── Applications API ──────────────────────────────────────────

@app.route("/api/applied/<job_id>", methods=["POST"])
def api_add_application(job_id):
    """Mark a job as applied."""
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    added = storage.add_application(job_id, notes)
    if added:
        logger.info("Applied: %s%s", job_id[:12], f"  notes={notes!r}" if notes else "")
    return jsonify({"status": "added" if added else "already_exists", "job_id": job_id})


@app.route("/api/applied/<job_id>", methods=["DELETE"])
def api_remove_application(job_id):
    """Un-mark a job as applied."""
    removed = storage.remove_application(job_id)
    if removed:
        logger.info("Un-applied: %s", job_id[:12])
    return jsonify({"status": "removed" if removed else "not_found", "job_id": job_id})


@app.route("/api/applied/<job_id>/notes", methods=["PUT"])
def api_update_application_notes(job_id):
    """Update notes on an application."""
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    updated = storage.update_application_notes(job_id, notes)
    if not updated:
        return jsonify({"error": "Application not found"}), 404
    return jsonify({"status": "updated", "job_id": job_id})


@app.route("/api/applications")
def api_applications():
    """Get all applied jobs."""
    sort_by = request.args.get("sort_by", "applied_at")
    order = request.args.get("order", "desc")
    jobs = storage.get_applications(sort_by=sort_by, ascending=(order == "asc"))
    return jsonify({"jobs": jobs, "total": len(jobs)})


# ── Not Interested API ─────────────────────────────────────────

@app.route("/api/not-interested/<job_id>", methods=["POST"])
def api_add_not_interested(job_id):
    """Mark a job as not interested."""
    added = storage.add_not_interested(job_id)
    if added:
        logger.info("Not interested: %s", job_id[:12])
    return jsonify({"status": "added" if added else "already_exists", "job_id": job_id})


@app.route("/api/not-interested/<job_id>", methods=["DELETE"])
def api_remove_not_interested(job_id):
    """Remove not interested status."""
    removed = storage.remove_not_interested(job_id)
    if removed:
        logger.info("Un-not-interested: %s", job_id[:12])
    return jsonify({"status": "removed" if removed else "not_found", "job_id": job_id})


# ── Regions API ────────────────────────────────────────────────

@app.route("/api/regions")
def api_regions():
    """Return available region labels for the filter dropdown."""
    from job_scraper.storage import _REGION_PATTERNS
    regions = sorted(_REGION_PATTERNS.keys(), key=str.lower)
    return jsonify({"regions": regions})


# ── Notes API ──────────────────────────────────────────────────

@app.route("/api/notes", methods=["GET"])
def api_notes_list():
    """List all notes."""
    query = request.args.get("q", "")
    sort_by = request.args.get("sort_by", "updated_at")
    order = request.args.get("order", "desc")
    notes = storage.get_notes(query=query, sort_by=sort_by, ascending=(order == "asc"))
    return jsonify({"notes": notes, "total": len(notes)})


@app.route("/api/notes", methods=["POST"])
def api_create_note():
    """Create a new note."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    body = data.get("body", "")
    if not title:
        return jsonify({"error": "Title is required"}), 400
    note_id = storage.create_note(title, body)
    logger.info("Note created: #%d %r", note_id, title[:40])
    return jsonify({"status": "created", "id": note_id}), 201


@app.route("/api/notes/<int:note_id>", methods=["GET"])
def api_get_note(note_id):
    """Get a single note."""
    note = storage.get_note(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404
    return jsonify(note)


@app.route("/api/notes/<int:note_id>", methods=["PUT"])
def api_update_note(note_id):
    """Update an existing note."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    body = data.get("body", "")
    if not title:
        return jsonify({"error": "Title is required"}), 400
    updated = storage.update_note(note_id, title, body)
    if not updated:
        return jsonify({"error": "Note not found"}), 404
    logger.info("Note updated: #%d %r", note_id, title[:40])
    return jsonify({"status": "updated", "id": note_id})


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
def api_delete_note(note_id):
    """Delete a note."""
    removed = storage.delete_note(note_id)
    if not removed:
        return jsonify({"error": "Note not found"}), 404
    logger.info("Note deleted: #%d", note_id)
    return jsonify({"status": "deleted", "id": note_id})


# ── Saved Searches API ─────────────────────────────────────────

@app.route("/api/saved-searches", methods=["GET"])
def api_saved_searches_list():
    """List all saved searches."""
    searches = storage.get_saved_searches()
    return jsonify({"searches": searches, "total": len(searches)})


@app.route("/api/saved-searches", methods=["POST"])
def api_create_saved_search():
    """Save a search configuration."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    params = data.get("params", {})
    if not name:
        return jsonify({"error": "Name is required"}), 400
    search_id = storage.create_saved_search(name, params)
    logger.info("Saved search created: #%d %r", search_id, name[:40])
    return jsonify({"status": "created", "id": search_id}), 201


@app.route("/api/saved-searches/<int:search_id>", methods=["GET"])
def api_get_saved_search(search_id):
    """Get a single saved search."""
    search = storage.get_saved_search(search_id)
    if not search:
        return jsonify({"error": "Saved search not found"}), 404
    return jsonify(search)


@app.route("/api/saved-searches/<int:search_id>", methods=["PUT"])
def api_update_saved_search(search_id):
    """Update a saved search."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    params = data.get("params", {})
    if not name:
        return jsonify({"error": "Name is required"}), 400
    updated = storage.update_saved_search(search_id, name, params)
    if not updated:
        return jsonify({"error": "Saved search not found"}), 404
    logger.info("Saved search updated: #%d %r", search_id, name[:40])
    return jsonify({"status": "updated", "id": search_id})


@app.route("/api/saved-searches/<int:search_id>", methods=["DELETE"])
def api_delete_saved_search(search_id):
    """Delete a saved search."""
    removed = storage.delete_saved_search(search_id)
    if not removed:
        return jsonify({"error": "Saved search not found"}), 404
    logger.info("Saved search deleted: #%d", search_id)
    return jsonify({"status": "deleted", "id": search_id})


# ── Saved Board Searches API ───────────────────────────────────

@app.route("/api/saved-board-searches", methods=["GET"])
def api_saved_board_searches_list():
    """List all saved board searches."""
    searches = storage.get_saved_board_searches()
    return jsonify({"searches": searches, "total": len(searches)})


@app.route("/api/saved-board-searches", methods=["POST"])
def api_create_saved_board_search():
    """Save a board filter configuration."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    params = data.get("params", {})
    if not name:
        return jsonify({"error": "Name is required"}), 400
    search_id = storage.create_saved_board_search(name, params)
    logger.info("Saved board search created: #%d %r", search_id, name[:40])
    return jsonify({"status": "created", "id": search_id}), 201


@app.route("/api/saved-board-searches/<int:search_id>", methods=["GET"])
def api_get_saved_board_search(search_id):
    """Get a single saved board search."""
    search = storage.get_saved_board_search(search_id)
    if not search:
        return jsonify({"error": "Saved board search not found"}), 404
    return jsonify(search)


@app.route("/api/saved-board-searches/<int:search_id>", methods=["PUT"])
def api_update_saved_board_search(search_id):
    """Update a saved board search."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    params = data.get("params", {})
    if not name:
        return jsonify({"error": "Name is required"}), 400
    updated = storage.update_saved_board_search(search_id, name, params)
    if not updated:
        return jsonify({"error": "Saved board search not found"}), 404
    logger.info("Saved board search updated: #%d %r", search_id, name[:40])
    return jsonify({"status": "updated", "id": search_id})


@app.route("/api/saved-board-searches/<int:search_id>", methods=["DELETE"])
def api_delete_saved_board_search(search_id):
    """Delete a saved board search."""
    removed = storage.delete_saved_board_search(search_id)
    if not removed:
        return jsonify({"error": "Saved board search not found"}), 404
    logger.info("Saved board search deleted: #%d", search_id)
    return jsonify({"status": "deleted", "id": search_id})


# ── AI Prompts API ─────────────────────────────────────────────

@app.route("/api/ai-prompts", methods=["GET"])
def api_ai_prompts_list():
    """List all AI prompt configurations."""
    prompts = storage.get_ai_prompts()
    return jsonify({"prompts": prompts, "total": len(prompts)})


@app.route("/api/ai-prompts", methods=["POST"])
def api_create_ai_prompt():
    """Create a new AI prompt configuration."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    model = data.get("model", "").strip()
    cv = data.get("cv", "").strip()
    about_me = data.get("about_me", "").strip()
    preferences = data.get("preferences", "").strip()
    extra_context = data.get("extra_context", "").strip()
    is_active = bool(data.get("is_active", False))

    if not title:
        return jsonify({"error": "Title is required"}), 400
    if not model:
        return jsonify({"error": "Model is required"}), 400

    prompt_id = storage.create_ai_prompt(
        title=title,
        model=model,
        cv=cv,
        about_me=about_me,
        preferences=preferences,
        extra_context=extra_context,
        is_active=is_active,
    )
    logger.info("AI prompt created: #%d %r  model=%s", prompt_id, title[:40], model)
    return jsonify({"status": "created", "id": prompt_id}), 201


@app.route("/api/ai-prompts/<int:prompt_id>", methods=["GET"])
def api_get_ai_prompt(prompt_id):
    """Get a single AI prompt configuration."""
    prompt = storage.get_ai_prompt(prompt_id)
    if not prompt:
        return jsonify({"error": "AI prompt not found"}), 404
    return jsonify(prompt)


@app.route("/api/ai-prompts/<int:prompt_id>", methods=["PUT"])
def api_update_ai_prompt(prompt_id):
    """Update an existing AI prompt configuration."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    model = data.get("model", "").strip()
    cv = data.get("cv", "").strip()
    about_me = data.get("about_me", "").strip()
    preferences = data.get("preferences", "").strip()
    extra_context = data.get("extra_context", "").strip()
    is_active = bool(data.get("is_active", False))

    if not title:
        return jsonify({"error": "Title is required"}), 400
    if not model:
        return jsonify({"error": "Model is required"}), 400

    updated = storage.update_ai_prompt(
        prompt_id=prompt_id,
        title=title,
        model=model,
        cv=cv,
        about_me=about_me,
        preferences=preferences,
        extra_context=extra_context,
        is_active=is_active,
    )
    if not updated:
        return jsonify({"error": "AI prompt not found"}), 404
    logger.info("AI prompt updated: #%d %r  model=%s", prompt_id, title[:40], model)
    return jsonify({"status": "updated", "id": prompt_id})


@app.route("/api/ai-prompts/<int:prompt_id>", methods=["DELETE"])
def api_delete_ai_prompt(prompt_id):
    """Delete an AI prompt configuration."""
    removed = storage.delete_ai_prompt(prompt_id)
    if not removed:
        return jsonify({"error": "AI prompt not found"}), 404
    logger.info("AI prompt deleted: #%d", prompt_id)
    return jsonify({"status": "deleted", "id": prompt_id})


@app.route("/api/ai-prompts/<int:prompt_id>/activate", methods=["POST"])
def api_activate_ai_prompt(prompt_id):
    """Set an AI prompt as the active default."""
    updated = storage.set_active_ai_prompt(prompt_id)
    if not updated:
        return jsonify({"error": "AI prompt not found"}), 404
    logger.info("AI prompt activated: #%d", prompt_id)
    return jsonify({"status": "activated", "id": prompt_id})


# ── Ollama API ──────────────────────────────────────────────────

@app.route("/api/ollama/models")
def api_ollama_models():
    """
    Return all available models grouped by source:
      local_models  – models installed in the local Ollama instance
      owui_models   – models available through Open WebUI (Gemini, etc.)
      cloud_models  – curated list of direct cloud API models
    """
    # ── Local Ollama models ────────────────────────────────────
    ollama_available = False
    local_models: list[str] = []
    try:
        url = config.OLLAMA_BASE_URL.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read().decode())
        local_models = sorted(m["name"] for m in data.get("models", []))
        ollama_available = True
    except urllib.error.URLError as exc:
        logger.warning("Ollama unreachable: %s", exc)
    except Exception as exc:
        logger.warning("Ollama model fetch failed: %s", exc)

    # ── Open WebUI models ──────────────────────────────────────
    # Open WebUI exposes an OpenAI-compatible GET /api/models endpoint that
    # returns ALL models it knows about — local Ollama + connected cloud
    # providers (Gemini, Claude, etc.) configured inside Open WebUI.
    owui_available = False
    owui_models: list[dict] = []
    owui_base = config.OPEN_WEBUI_BASE_URL.rstrip("/")
    if owui_base:
        try:
            owui_url = f"{owui_base}/api/models"
            owui_req = urllib.request.Request(owui_url, method="GET")
            if config.OPEN_WEBUI_API_KEY:
                owui_req.add_header("Authorization", f"Bearer {config.OPEN_WEBUI_API_KEY}")
            with urllib.request.urlopen(owui_req, timeout=5) as resp:
                owui_data = _json.loads(resp.read().decode())
            # Response has a "data" list; each item has "id" and "name"
            local_ids = set(local_models)
            for m in owui_data.get("data", []):
                mid  = m.get("id", "")
                name = m.get("name") or mid
                # Skip bare Ollama models already shown in local_models to
                # avoid duplicates (Open WebUI mirrors Ollama models too)
                if mid in local_ids:
                    continue
                owui_models.append({"id": mid, "label": name})
            owui_models.sort(key=lambda x: x["label"].lower())
            owui_available = True
        except urllib.error.URLError as exc:
            logger.warning("Open WebUI unreachable at %s: %s", owui_base, exc)
        except Exception as exc:
            logger.warning("Open WebUI model fetch failed: %s", exc)

    # ── Direct cloud models (hardcoded list) ───────────────────
    _key_map = {
        "openai":    bool(config.OPENAI_API_KEY),
        "anthropic": bool(config.ANTHROPIC_API_KEY),
        "google":    bool(config.GOOGLE_AI_API_KEY),
    }
    cloud_models = [
        {**m, "has_key": _key_map.get(m["provider"], False)}
        for m in config.CLOUD_MODELS
    ]

    return jsonify({
        "available":      ollama_available,
        "local_models":   local_models,
        "owui_models":    owui_models,
        "owui_available": owui_available,
        "cloud_models":   cloud_models,
        "models":         local_models,   # legacy field
    })


# ── AI Analysis endpoint ───────────────────────────────────────

@app.route("/api/ai-analyse", methods=["POST"])
def api_ai_analyse():
    """
    Run an AI analysis of a job against a saved prompt configuration.
    Calls Ollama synchronously (the fetch on the client is async/background).
    Returns the analysis id and key headline fields on success, or a detailed
    error payload that the client can surface as a notification.
    """
    data      = request.get_json(silent=True) or {}
    job_id    = data.get("job_id",    "").strip()
    prompt_id = data.get("prompt_id")

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400
    if not prompt_id:
        return jsonify({"error": "prompt_id is required"}), 400

    job = storage.get_job(job_id)
    if not job:
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    prompt_config = storage.get_ai_prompt(int(prompt_id))
    if not prompt_config:
        return jsonify({"error": f"AI prompt not found: {prompt_id}"}), 404

    model = (prompt_config.get("model") or "").strip()
    if not model:
        return jsonify({"error": "The selected AI prompt has no model configured"}), 400

    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user",   "content": _build_analysis_user_message(prompt_config, job)},
    ]

    logger.info(
        "AI analysis starting – job=%s prompt=#%s model=%s",
        job_id[:12], prompt_id, model,
    )

    # Log the exact prompt being sent so it can be reviewed alongside the response.
    _log_llm_request(
        job_id=job_id,
        prompt_id=int(prompt_id),
        prompt_title=prompt_config.get("title", ""),
        model=model,
        messages=messages,
    )

    # ── Call LLM (Ollama or cloud provider) ───────────────────
    try:
        raw_content = _call_model(model, messages)
    except RuntimeError as exc:
        logger.warning("Ollama call failed: %s", exc)
        return jsonify({"error": str(exc)}), 502

    # Log the full raw response unconditionally so every Ollama reply is
    # auditable, regardless of whether JSON extraction/validation succeeds.
    _log_llm_response(
        job_id=job_id,
        prompt_id=int(prompt_id),
        prompt_title=prompt_config.get("title", ""),
        model=model,
        raw_response=raw_content,
    )

    # ── Extract JSON ───────────────────────────────────────────
    try:
        analysis_data = _extract_json(raw_content)
    except ValueError as exc:
        preview = raw_content[:300].replace("\n", " ")
        logger.warning("JSON extraction failed – %s | preview: %s", exc, preview)
        return jsonify({
            "error": f"Model did not return valid JSON: {exc}",
            "raw_preview": preview,
        }), 422

    # ── Validate schema ────────────────────────────────────────
    errors = _validate_analysis(analysis_data)
    if errors:
        preview = raw_content[:300].replace("\n", " ")
        logger.warning("Analysis validation failed – %s | preview: %s", errors, preview)
        return jsonify({
            "error": f"Analysis response failed validation: {'; '.join(errors)}",
            "validation_errors": errors,
            "raw_preview": preview,
        }), 422

    # ── Persist ────────────────────────────────────────────────
    analysis_id = storage.save_ai_analysis(
        job_id=job_id,
        prompt_id=int(prompt_id),
        model=model,
        result=analysis_data,
    )

    logger.info(
        "AI analysis saved – #%d job=%s score=%s rec=%s",
        analysis_id, job_id[:12],
        analysis_data.get("match_score"), analysis_data.get("recommendation"),
    )

    return jsonify({
        "status":       "completed",
        "analysis_id":  analysis_id,
        "match_score":  analysis_data.get("match_score"),
        "recommendation": analysis_data.get("recommendation"),
        "job_summary":  analysis_data.get("job_summary", ""),
    })


@app.route("/api/ai-analyses")
def api_ai_analyses_list():
    """
    Return a paginated, filtered list of all AI analyses joined with job data.
    Query params: query, min_score, recommendation (comma-sep), prompt_id, limit, offset.
    """
    query        = request.args.get("query", "").strip()
    min_score    = int(request.args.get("min_score", 0) or 0)
    rec_raw      = request.args.get("recommendation", "").strip()
    recommendations = [r.strip() for r in rec_raw.split(",") if r.strip()] if rec_raw else []
    prompt_id    = request.args.get("prompt_id", type=int)
    limit        = min(int(request.args.get("limit", 50) or 50), 200)
    offset       = int(request.args.get("offset", 0) or 0)

    analyses, total = storage.get_ai_analyses_list(
        query=query,
        min_score=min_score,
        recommendations=recommendations,
        prompt_id=prompt_id,
        limit=limit,
        offset=offset,
    )
    return jsonify({"analyses": analyses, "total": total, "offset": offset, "limit": limit})


@app.route("/api/ai-analyses/<job_id>")
def api_ai_analyses_for_job(job_id):
    """Return all AI analyses for a given job."""
    analyses = storage.get_ai_analyses_for_job(job_id)
    return jsonify({"analyses": analyses, "total": len(analyses)})


# ── helpers ────────────────────────────────────────────────────

def _get_sources_info() -> list[dict]:
    """Build a list of sources with availability info."""
    info = []
    for name, cls in ALL_SOURCES.items():
        instance = cls()
        info.append({
            "name": name,
            "available": instance.is_available(),
            "requires_key": instance.requires_api_key,
            "free": name in FREE_SOURCES,
        })
    return info


# ── Startup ────────────────────────────────────────────────────

def _print_startup_banner():
    """Log useful info on startup."""
    from job_scraper.storage import check_db_connection

    sources_info = _get_sources_info()
    available = [s["name"] for s in sources_info if s["available"]]
    unavailable = [s["name"] for s in sources_info if not s["available"]]

    db_ok, db_err = check_db_connection()

    banner = [
        f"  Database:   {'Connected' if db_ok else 'UNAVAILABLE – ' + db_err}",
        f"  Sources:    {len(available)} available, {len(unavailable)} unavailable",
        f"  Available:  {', '.join(available) if available else '(none)'}",
    ]
    if unavailable:
        banner.append(f"  Skipped:    {', '.join(unavailable)}  (missing API key or dependency)")
    banner += [
        "",
        f"  Server:     http://localhost:5000",
        "",
    ]
    for line in banner:
        logger.info(line)


if __name__ == "__main__":
    import os
    # Only print banner in the reloader child process (avoids printing twice)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        _print_startup_banner()
    app.run(debug=True, host="0.0.0.0", port=5000)
