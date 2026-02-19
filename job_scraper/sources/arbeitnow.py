"""
Arbeitnow – free API, no key required.
Endpoint: https://www.arbeitnow.com/api/job-board-api
Paginated; we iterate pages and filter by keywords.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class ArbeitnowSource(BaseSource):
    name = "Arbeitnow"
    requires_api_key = False
    base_url = "https://www.arbeitnow.com/api/job-board-api"

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
        page = 1
        max_pages = 5  # safety cap

        while len(jobs) < max_results and page <= max_pages:
            try:
                resp = self._get(self.base_url, params={"page": page})
                payload = resp.json()
            except Exception as exc:
                logger.error("[%s] Page %d failed: %s", self.name, page, exc)
                break

            listings = payload.get("data", [])
            if not listings:
                break

            for item in listings:
                if len(jobs) >= max_results:
                    break

                title = item.get("title", "")
                company = item.get("company_name", "")
                description = item.get("description", "")
                tags = item.get("tags", [])
                tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
                is_remote = item.get("remote", False)

                # Remote filter
                if remote == "Remote" and not is_remote:
                    continue
                if remote == "On-site" and is_remote:
                    continue

                # Keyword filter
                searchable = f"{title} {company} {description} {tags_str}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                slug = item.get("slug", "")
                url = item.get("url", f"https://www.arbeitnow.com/view/{slug}")

                job_types = item.get("job_types", [])
                jt = ", ".join(job_types) if isinstance(job_types, list) else str(job_types)

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=item.get("location", ""),
                    description=self._clean_html(description),
                    url=url,
                    source=self.name,
                    remote="Remote" if is_remote else "On-site",
                    job_type=jt or job_type,
                    date_posted=item.get("created_at", ""),
                    tags=tags_str,
                ))

            # Check for next page
            next_url = payload.get("links", {}).get("next")
            if not next_url:
                break
            page += 1

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
