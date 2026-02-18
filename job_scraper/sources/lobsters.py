"""
Lobste.rs – free, no key. Job tag RSS feed.
Tech/employment posts from the Lobste.rs community.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

JOB_RSS = "https://lobste.rs/t/job.rss"


class LobstersSource(BaseSource):
    name = "Lobsters"
    requires_api_key = False
    base_url = "https://lobste.rs"

    def fetch_jobs(
        self,
        keywords: List[str],
        location: str = "",
        remote: str = "Any",
        job_type: str = "",
        salary_min: Optional[float] = None,
        experience_level: str = "",
        max_results: int = 100,
        posted_in_last_days: Optional[int] = None,
    ) -> List[Job]:
        try:
            import feedparser
        except ImportError:
            logger.warning("[%s] feedparser not installed", self.name)
            return []

        jobs: List[Job] = []
        try:
            feed = feedparser.parse(JOB_RSS)
        except Exception as exc:
            logger.error("[%s] Failed to parse RSS: %s", self.name, exc)
            return []

        for entry in feed.entries:
            if len(jobs) >= max_results:
                break

            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            published = entry.get("published", "")

            searchable = f"{title} {summary}"
            if not self._matches_keywords(searchable, keywords):
                continue

            jobs.append(Job(
                title=title,
                company="",
                location="",
                description=self._clean_html(summary),
                url=link,
                source=self.name,
                remote="Unknown",
                date_posted=published,
                tags="lobsters, job",
            ))

        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
