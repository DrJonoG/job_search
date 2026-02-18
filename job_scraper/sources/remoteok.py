"""
RemoteOK – free API, no key required.
Endpoint: https://remoteok.com/api
Returns all remote jobs; we filter client-side by keywords.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class RemoteOKSource(BaseSource):
    name = "RemoteOK"
    requires_api_key = False
    base_url = "https://remoteok.com/api"

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
        # RemoteOK only has remote jobs – skip if user wants on-site only
        if remote == "On-site":
            return []

        try:
            resp = self._get(self.base_url)
            data = resp.json()
        except Exception as exc:
            logger.error("[%s] Failed to fetch: %s", self.name, exc)
            return []

        # First element is a legal notice / metadata – skip it
        listings = data[1:] if isinstance(data, list) and len(data) > 1 else []

        jobs: List[Job] = []
        for item in listings:
            if len(jobs) >= max_results:
                break

            title = item.get("position", "")
            company = item.get("company", "")
            description = item.get("description", "")
            tags = item.get("tags", [])
            tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

            # Keyword filtering
            searchable = f"{title} {company} {description} {tags_str}"
            if not self._matches_keywords(searchable, keywords):
                continue

            # Salary filtering
            s_min = self._safe_float(item.get("salary_min"))
            s_max = self._safe_float(item.get("salary_max"))
            if salary_min and s_max and s_max < salary_min:
                continue

            url = item.get("apply_url") or item.get("url", "")
            if url and not url.startswith("http"):
                url = f"https://remoteok.com{url}"

            jobs.append(Job(
                title=title,
                company=company,
                location=item.get("location", "Remote"),
                description=self._clean_html(description),
                url=url,
                source=self.name,
                remote="Remote",
                salary_min=s_min,
                salary_max=s_max,
                salary_currency="USD",
                job_type=job_type or "Full-time",
                date_posted=item.get("date", ""),
                tags=tags_str,
                company_logo=item.get("company_logo", ""),
            ))

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
