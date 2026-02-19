"""
Reed.co.uk – requires free API key.
Register at https://www.reed.co.uk/developers/jobseeker
Endpoint: https://www.reed.co.uk/api/1.0/search
Authentication: Basic Auth (API key as username, empty password).
"""

from __future__ import annotations

import logging
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class ReedSource(BaseSource):
    name = "Reed"
    requires_api_key = True
    base_url = "https://www.reed.co.uk/api/1.0/search"

    def is_available(self) -> bool:
        return bool(config.REED_API_KEY)

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
        if not self.is_available():
            logger.info("[%s] Skipped – API key not configured", self.name)
            return []

        jobs: List[Job] = []
        results_per_request = 100  # Reed API max per request

        for keyword in keywords:
            jobs_before_keyword = len(jobs)

            skip = 0
            while len(jobs) - jobs_before_keyword < max_results:
                remaining = max_results - (len(jobs) - jobs_before_keyword)
                params = {
                    "keywords": keyword,
                    "resultsToTake": min(results_per_request, remaining),
                    "resultsToSkip": skip,
                }

                if location:
                    params["locationName"] = location
                if salary_min:
                    params["minimumSalary"] = int(salary_min)

                # Map job type
                if job_type:
                    jt_lower = job_type.lower()
                    if "full" in jt_lower:
                        params["fullTime"] = "true"
                    elif "part" in jt_lower:
                        params["partTime"] = "true"
                    elif "contract" in jt_lower:
                        params["contract"] = "true"

                try:
                    resp = self._get(
                        self.base_url,
                        params=params,
                        auth=(config.REED_API_KEY, ""),
                    )
                    payload = resp.json()
                except Exception as exc:
                    logger.error("[%s] Search for '%s' failed: %s", self.name, keyword, exc)
                    break

                results = payload.get("results", []) if isinstance(payload, dict) else payload
                if not isinstance(results, list) or not results:
                    break

                for item in results:
                    if len(jobs) - jobs_before_keyword >= max_results:
                        break

                    title = item.get("jobTitle", "")
                    description = item.get("jobDescription", "")
                    job_url = item.get("jobUrl", "")

                    text_lower = f"{title} {description}".lower()
                    is_remote = "remote" in text_lower
                    if remote == "Remote" and not is_remote:
                        continue
                    if remote == "On-site" and is_remote:
                        continue

                    s_min = self._safe_float(item.get("minimumSalary"))
                    s_max = self._safe_float(item.get("maximumSalary"))
                    if salary_min and s_max and s_max < salary_min:
                        continue

                    jobs.append(Job(
                        title=title,
                        company=item.get("employerName", ""),
                        location=item.get("locationName", ""),
                        description=self._clean_html(description),
                        url=job_url,
                        source=self.name,
                        remote="Remote" if is_remote else "On-site",
                        salary_min=s_min,
                        salary_max=s_max,
                        salary_currency="GBP",
                        job_type=job_type,
                        date_posted=item.get("date", ""),
                        tags="",
                    ))

                skip += len(results)
                if len(results) < results_per_request:
                    break

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
