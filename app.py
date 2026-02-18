"""
Job Search Tool – Flask web application.

Routes:
  /                     Dashboard & search form
  /jobs                 Browse saved jobs
  /favourites           Browse favourite jobs
  /applied              Browse applied jobs
  /notes                Browse & manage notes
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
"""

from __future__ import annotations

import logging
import math
import time

from flask import Flask, render_template, request, jsonify, Response, g

import config
from job_scraper.storage import JobStorage, DatabaseUnavailable
from job_scraper.manager import SearchManager
from job_scraper.sources import ALL_SOURCES, FREE_SOURCES, API_KEY_SOURCES

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
