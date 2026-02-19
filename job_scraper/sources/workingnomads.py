"""
Working Nomads – free API, no key required.
Endpoint: https://www.workingnomads.com/api/exposed_jobs
Remote jobs across tech, customer success, sales, etc.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class WorkingNomadsSource(BaseSource):
    name = "WorkingNomads"
    requires_api_key = False
    base_url = "https://www.workingnomads.com/api/exposed_jobs"

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
        if remote == "On-site":
            return []

        jobs: List[Job] = []
        try:
            resp = self._get(self.base_url)
            data = resp.json()
        except Exception as exc:
            logger.error("[%s] Failed to fetch: %s", self.name, exc)
            return []

        if not isinstance(data, list):
            return []

        for item in data:
            if len(jobs) >= max_results:
                break

            title = item.get("title", "")
            company = item.get("company_name", "")
            description = item.get("description", "")
            url = item.get("url", "")
            loc = item.get("location", "Remote")
            tags = item.get("tags", "")
            if isinstance(tags, list):
                tags = ", ".join(tags) if tags else ""
            pub_date = item.get("pub_date", "")
            category = item.get("category_name", "")

            searchable = f"{title} {company} {description} {tags} {category}"
            if not self._matches_keywords(searchable, keywords):
                continue

            jobs.append(Job(
                title=title,
                company=company,
                location=loc,
                description=self._clean_html(description),
                url=url,
                source=self.name,
                remote="Remote",
                job_type=job_type or "Full-time",
                date_posted=pub_date[:10] if pub_date else "",
                tags=tags or category,
            ))

        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
