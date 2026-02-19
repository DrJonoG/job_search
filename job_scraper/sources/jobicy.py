"""
Jobicy – free API, no key required.
Endpoint: https://jobicy.com/api/v2/remote-jobs
Supports count, geo, industry, tag parameters.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)


class JobicySource(BaseSource):
    name = "Jobicy"
    requires_api_key = False
    base_url = "https://jobicy.com/api/v2/remote-jobs"

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
        # Jobicy is remote-only – skip for on-site requests
        if remote == "On-site":
            return []

        jobs: List[Job] = []
        keywords_list = normalize_keywords(keywords, default=[""])
        if not keywords_list or keywords_list == [""]:
            keywords_list = [""]

        for keyword in keywords_list:
            jobs_before_keyword = len(jobs)

            params: dict = {"count": min(max_results, 50)}
            if location:
                params["geo"] = location
            if keyword:
                params["tag"] = keyword

            try:
                resp = self._get(self.base_url, params=params)
                payload = resp.json()
            except Exception as exc:
                logger.error("[%s] Failed to fetch for '%s': %s", self.name, keyword or "(all)", exc)
                continue

            listings = payload.get("jobs", [])

            for item in listings:
                if len(jobs) - jobs_before_keyword >= max_results:
                    break

            title = item.get("jobTitle", "")
            company = item.get("companyName", "")
            description = item.get("jobDescription", "")
            geo = item.get("jobGeo", "Remote")
            jt = item.get("jobType", "")
            job_url = item.get("url", "")

            # Salary
            s_min = self._safe_float(item.get("annualSalaryMin"))
            s_max = self._safe_float(item.get("annualSalaryMax"))
            s_currency = item.get("salaryCurrency", "USD")

            if salary_min and s_max and s_max < salary_min:
                continue

            # Keyword filter
            searchable = f"{title} {company} {description} {geo} {jt}"
            if not self._matches_keywords(searchable, keywords):
                continue

            industry = item.get("jobIndustry", [])
            industry_str = ", ".join(industry) if isinstance(industry, list) else str(industry)

            jobs.append(Job(
                title=title,
                company=company,
                location=geo,
                description=self._clean_html(description),
                url=job_url,
                source=self.name,
                remote="Remote",
                salary_min=s_min,
                salary_max=s_max,
                salary_currency=s_currency,
                job_type=jt,
                date_posted=item.get("pubDate", ""),
                tags=industry_str,
                company_logo=item.get("companyLogo", ""),
            ))

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
