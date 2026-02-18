"""
Findwork.dev – free API key required.
Register at https://findwork.dev/developers/
Endpoint: GET https://findwork.dev/api/jobs/
Focused on developer / tech jobs worldwide.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class FindworkSource(BaseSource):
    name = "Findwork"
    requires_api_key = True
    base_url = "https://findwork.dev/api/jobs/"

    def is_available(self) -> bool:
        return bool(config.FINDWORK_API_KEY)

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
        if not self.is_available():
            logger.info("[%s] Skipped – API key not configured", self.name)
            return []

        jobs: List[Job] = []

        for keyword in keywords:
            jobs_before_keyword = len(jobs)

            params = {
                "search": keyword,
            }

            if location:
                params["location"] = location
            if remote == "Remote":
                params["remote"] = "true"

            # Sort by most recent
            params["sort_by"] = "relevance"

            headers = {
                "Authorization": f"Token {config.FINDWORK_API_KEY}",
            }

            page_url = self.base_url
            page_count = 0

            while page_url and len(jobs) - jobs_before_keyword < max_results and page_count < 50:
                try:
                    resp = self.session.get(
                        page_url,
                        params=params if page_count == 0 else None,
                        headers=headers,
                        timeout=self.timeout,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.error("[%s] Search for '%s' failed: %s", self.name, keyword, exc)
                    break

                results = data.get("results", [])
                if not results:
                    break

                for item in results:
                    if len(jobs) - jobs_before_keyword >= max_results:
                        break

                    title = item.get("role", "")
                    company = item.get("company_name", "")
                    loc = item.get("location", "")
                    description = item.get("text", "") or item.get("description", "")
                    job_url = item.get("url", "")
                    is_remote = item.get("remote", False)
                    date_posted = item.get("date_posted", "")
                    keywords_list = item.get("keywords", [])

                    if remote == "Remote" and not is_remote:
                        continue
                    if remote == "On-site" and is_remote:
                        continue

                    # Salary
                    s_min = self._safe_float(item.get("salary_min"))
                    s_max = self._safe_float(item.get("salary_max"))
                    if salary_min and s_max and s_max < salary_min:
                        continue

                    tags = ", ".join(keywords_list) if isinstance(keywords_list, list) else str(keywords_list)

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=loc or ("Remote" if is_remote else ""),
                        description=self._clean_html(description),
                        url=job_url,
                        source=self.name,
                        remote="Remote" if is_remote else "On-site",
                        salary_min=s_min,
                        salary_max=s_max,
                        job_type=item.get("employment_type", job_type),
                        date_posted=date_posted,
                        tags=tags,
                        company_logo=item.get("company_logo", ""),
                    ))

                page_url = data.get("next")
                page_count += 1
                params = {}  # next URL already has params

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
