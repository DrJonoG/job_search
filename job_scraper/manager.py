"""
Search Manager – orchestrates searches across all configured sources.

• Runs each source in a thread pool for speed.
• Persists jobs to the database as each source completes (crash-safe).
• Exposes progress for the web UI to poll.
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import Thread
from typing import Dict, List, Optional

import config
from .storage import JobStorage
from .sources import ALL_SOURCES

logger = logging.getLogger(__name__)


@dataclass
class SearchTask:
    """Tracks the state of a running (or finished) search."""

    task_id: str
    status: str = "pending"             # pending | running | completed | failed | cancelled
    cancelled: bool = False
    total_sources: int = 0
    completed_sources: int = 0
    current_source: str = ""
    jobs_found: int = 0
    new_jobs_saved: int = 0
    errors: List[str] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    source_results: Dict[str, int] = field(default_factory=dict)
    source_status: Dict[str, dict] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1) if self.started_at else 0.0

    def to_dict(self) -> dict:
        now = time.time()
        source_info = {}
        for name, info in self.source_status.items():
            entry = {"status": info["status"]}
            if info.get("started_at"):
                end = info.get("finished_at") or now
                entry["elapsed_seconds"] = round(end - info["started_at"], 1)
            if info.get("jobs"):
                entry["jobs"] = info["jobs"]
            if info.get("error"):
                entry["error"] = info["error"]
            source_info[name] = entry
        return {
            "task_id": self.task_id,
            "status": self.status,
            "cancelled": self.cancelled,
            "total_sources": self.total_sources,
            "completed_sources": self.completed_sources,
            "current_source": self.current_source,
            "jobs_found": self.jobs_found,
            "new_jobs_saved": self.new_jobs_saved,
            "errors": self.errors,
            "elapsed_seconds": self.elapsed,
            "source_results": self.source_results,
            "source_status": source_info,
        }


class SearchManager:
    """Central coordinator for job searches."""

    def __init__(self, storage: JobStorage) -> None:
        self.storage = storage
        self._tasks: Dict[str, SearchTask] = {}

    # ── public API ─────────────────────────────────────────────
    def start_search(
        self,
        keywords: List[str],
        location: str = "",
        remote: str = "Any",
        job_type: str = "",
        salary_min: Optional[float] = None,
        experience_level: str = "",
        sources: Optional[List[str]] = None,
        max_results_per_source: int = 100,
        posted_in_last_days: Optional[int] = None,
    ) -> str:
        """
        Kick off a background search. Returns a task_id for polling.
        """
        task_id = uuid.uuid4().hex[:12]
        task = SearchTask(task_id=task_id)
        self._tasks[task_id] = task

        thread = Thread(
            target=self._run_search,
            kwargs={
                "task": task,
                "keywords": keywords,
                "location": location,
                "remote": remote,
                "job_type": job_type,
                "salary_min": salary_min,
                "experience_level": experience_level,
                "sources": sources,
                "max_results_per_source": max_results_per_source,
                "posted_in_last_days": posted_in_last_days,
            },
            daemon=True,
        )
        thread.start()
        return task_id

    def get_task(self, task_id: str) -> Optional[SearchTask]:
        return self._tasks.get(task_id)

    def cancel_search(self, task_id: str) -> bool:
        """Request cancellation of a running search. Returns True if task was found and cancelled."""
        task = self._tasks.get(task_id)
        if not task or task.status != "running":
            return False
        task.cancelled = True
        return True

    # ── internal ───────────────────────────────────────────────
    def _run_search(
        self,
        task: SearchTask,
        keywords: List[str],
        location: str,
        remote: str,
        job_type: str,
        salary_min: Optional[float],
        experience_level: str,
        sources: Optional[List[str]],
        max_results_per_source: int,
        posted_in_last_days: Optional[int],
    ) -> None:
        task.status = "running"
        task.started_at = time.time()

        # Determine which sources to use (dedupe; skip LinkedIn when JobSpy selected to avoid double scrape)
        raw_names = sources if sources else list(ALL_SOURCES.keys())
        has_jobspy = "JobSpy" in (raw_names or [])
        seen = set()
        source_names = []
        for name in raw_names:
            if not name or name in seen:
                continue
            if name == "LinkedIn" and has_jobspy:
                continue  # JobSpy already scrapes LinkedIn; avoid running both
            seen.add(name)
            source_names.append(name)
        active_sources = {}
        for name in source_names:
            cls = ALL_SOURCES.get(name)
            if cls is None:
                continue
            instance = cls()
            if instance.is_available():
                active_sources[name] = instance
            else:
                logger.info("Source '%s' skipped (not available / no API key)", name)

        task.total_sources = len(active_sources)

        if not active_sources:
            task.status = "failed"
            task.errors.append("No sources available. Check API key configuration.")
            task.finished_at = time.time()
            return

        # Initialise per-source tracking
        for name in active_sources:
            task.source_status[name] = {"status": "pending", "started_at": None, "finished_at": None}

        # Track which sources have used on_batch (incremental saving)
        _sources_using_batch: set = set()

        def _make_batch_callback(source_name):
            """Create a per-source on_batch callback that tracks usage."""
            def _save_batch(batch):
                if not batch:
                    return
                _sources_using_batch.add(source_name)
                task.jobs_found += len(batch)
                try:
                    task.new_jobs_saved += self.storage.save_jobs(batch)
                except Exception as exc:
                    task.errors.append(f"Storage error (batch): {exc}")
            return _save_batch

        def _fetch_from_source(name: str, source):
            start_time = time.time()
            task.source_status[name] = {"status": "running", "started_at": start_time, "finished_at": None}
            task.current_source = name
            logger.info("── [%s] STARTED ──", name)
            try:
                # Pass on_batch to every source so long-running ones (LinkedIn Direct,
                # ATS boards, etc.) can flush jobs to DB after each page/board.
                batch_cb = _make_batch_callback(name)
                results = source.fetch_jobs(
                    keywords=keywords,
                    location=location,
                    remote=remote,
                    job_type=job_type,
                    salary_min=salary_min,
                    experience_level=experience_level,
                    max_results=max_results_per_source,
                    posted_in_last_days=posted_in_last_days,
                    on_batch=batch_cb,
                )
                elapsed = round(time.time() - start_time, 1)
                task.source_status[name] = {
                    "status": "completed", "started_at": start_time,
                    "finished_at": time.time(), "jobs": len(results),
                }
                logger.info("── [%s] FINISHED ── %d jobs in %.1fs", name, len(results), elapsed)
                used_batch = name in _sources_using_batch
                return name, results, None, used_batch
            except Exception as exc:
                elapsed = round(time.time() - start_time, 1)
                task.source_status[name] = {
                    "status": "error", "started_at": start_time,
                    "finished_at": time.time(), "error": str(exc),
                }
                logger.exception("── [%s] FAILED ── after %.1fs: %s", name, elapsed, exc)
                return name, [], str(exc), False

        # Use thread pool for concurrent fetching (max 4 concurrent).
        # Save jobs to DB as each source completes so a crash doesn't lose results.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_from_source, name, src): name
                for name, src in active_sources.items()
            }
            for future in as_completed(futures):
                if getattr(task, "cancelled", False):
                    task.status = "cancelled"
                    task.finished_at = time.time()
                    logger.info("Search cancelled by user")
                    return
                name, results, error, used_batch = future.result()
                task.completed_sources += 1
                task.source_results[name] = len(results)

                if error:
                    task.errors.append(f"{name}: {error}")
                elif not used_batch:
                    task.jobs_found += len(results)
                    try:
                        new_count = self.storage.save_jobs(results)
                        task.new_jobs_saved += new_count
                    except Exception as exc:
                        task.errors.append(f"Storage error ({name}): {exc}")

                # Log remaining running sources for debugging hangs
                still_running = [
                    n for n, s in task.source_status.items()
                    if s["status"] == "running"
                ]
                if still_running:
                    logger.info(
                        "Progress: %d/%d complete. Still running: %s",
                        task.completed_sources, task.total_sources,
                        ", ".join(still_running),
                    )

        if getattr(task, "cancelled", False):
            task.status = "cancelled"
            task.finished_at = time.time()
            return

        task.status = "completed" if not task.errors else "completed"
        task.finished_at = time.time()

        # Summary
        logger.info("")
        logger.info("══════════════════════════════════════════════════")
        logger.info("  Search complete in %.1fs", task.elapsed)
        logger.info("  Total: %d jobs found, %d new saved to database", task.jobs_found, task.new_jobs_saved)
        if task.source_results:
            for src, count in sorted(task.source_results.items(), key=lambda x: -x[1]):
                status = task.source_status.get(src, {})
                elapsed_src = ""
                if status.get("started_at") and status.get("finished_at"):
                    elapsed_src = f"  ({round(status['finished_at'] - status['started_at'], 1)}s)"
                marker = "x" if status.get("status") == "error" else " "
                logger.info("    [%s] %-25s %4d jobs%s", marker, src, count, elapsed_src)
        if task.errors:
            logger.warning("  Errors (%d):", len(task.errors))
            for err in task.errors:
                logger.warning("    - %s", err)
        logger.info("══════════════════════════════════════════════════")
        logger.info("")
