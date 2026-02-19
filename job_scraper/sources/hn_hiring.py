"""
Hacker News "Who is hiring?" – via Algolia HN Search API (free, no key).
Returns the monthly "Who is hiring?" thread(s) as job entries so users can open the thread.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"


class HackerNewsHiringSource(BaseSource):
    name = "HN Who is hiring"
    requires_api_key = False
    base_url = "https://news.ycombinator.com"

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
        **kwargs,
    ) -> List[Job]:
        jobs: List[Job] = []
        try:
            resp = self._get(ALGOLIA_SEARCH, params={
                "query": "Who is hiring",
                "tags": "story",
                "hitsPerPage": 50,
            })
            data = resp.json()
        except Exception as exc:
            logger.error("[%s] Failed to fetch: %s", self.name, exc)
            return []

        hits = data.get("hits", []) if isinstance(data, dict) else []
        # Monthly thread title pattern: "Ask HN: Who is hiring? (Month YYYY)" or similar
        pattern = re.compile(r"who\s+is\s+hiring\?\s*\([^)]+\)", re.I)

        for hit in hits:
            if len(jobs) >= max_results:
                break
            title = hit.get("title", "")
            if not pattern.search(title) and "who is hiring" not in title.lower():
                continue
            # Prefer the canonical monthly thread format
            if "who is hiring?" not in title.lower():
                continue

            object_id = hit.get("objectID", "")
            story_id = hit.get("story_id", object_id)
            link = f"https://news.ycombinator.com/item?id={story_id}"
            created = hit.get("created_at", "")

            jobs.append(Job(
                title=title,
                company="Hacker News",
                location="",
                description="Monthly Hacker News 'Who is hiring?' thread. Click to open the thread and browse job postings in the comments.",
                url=link,
                source=self.name,
                remote="Unknown",
                date_posted=created[:10] if created else "",
                tags="hn, who is hiring, remote, tech",
            ))

        logger.info("[%s] Found %d threads", self.name, len(jobs))
        return jobs
