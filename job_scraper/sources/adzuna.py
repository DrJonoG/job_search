"""
Adzuna – requires free API key.
Register at https://developer.adzuna.com/
Endpoint: https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
"""

from __future__ import annotations

import logging
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)


class AdzunaSource(BaseSource):
    name = "Adzuna"
    requires_api_key = True
    base_url = "https://api.adzuna.com/v1/api/jobs"

    def is_available(self) -> bool:
        return bool(config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY)

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
        country: str = "gb",
    ) -> List[Job]:
        if not self.is_available():
            logger.info("[%s] Skipped – API keys not configured", self.name)
            return []

        jobs: List[Job] = []
        seen_urls: set = set()
        keywords_list = normalize_keywords(keywords)
        results_per_page = 50
        max_pages_per_keyword = max(1, max_results // results_per_page)

        for keyword in keywords_list:
            jobs_before_keyword = len(jobs)
            what = keyword

            for page in range(1, max_pages_per_keyword + 1):
                if len(jobs) - jobs_before_keyword >= max_results:
                    break

                params = {
                    "app_id": config.ADZUNA_APP_ID,
                    "app_key": config.ADZUNA_APP_KEY,
                    "what": what,
                    "results_per_page": results_per_page,
                    "content-type": "application/json",
                }

                if location:
                    params["where"] = location
                if salary_min:
                    params["salary_min"] = int(salary_min)

                url = f"{self.base_url}/{country}/search/{page}"

                try:
                    resp = self._get(url, params=params)
                    payload = resp.json()
                except Exception as exc:
                    logger.error("[%s] '%s' page %d failed: %s", self.name, keyword, page, exc)
                    break

                results = payload.get("results", [])
                if not results:
                    break

                for item in results:
                    if len(jobs) - jobs_before_keyword >= max_results:
                        break
                    job_url = item.get("redirect_url", "")
                    if job_url and job_url in seen_urls:
                        continue
                    if job_url:
                        seen_urls.add(job_url)

                    title = item.get("title", "")
                    company_obj = item.get("company", {})
                    company = company_obj.get("display_name", "") if isinstance(company_obj, dict) else ""
                    loc_obj = item.get("location", {})
                    loc_display = ""
                    if isinstance(loc_obj, dict):
                        areas = loc_obj.get("area", [])
                        loc_display = ", ".join(areas) if areas else loc_obj.get("display_name", "")

                    description = item.get("description", "")
                    s_min = self._safe_float(item.get("salary_min"))
                    s_max = self._safe_float(item.get("salary_max"))

                    category = item.get("category", {})
                    cat_label = category.get("label", "") if isinstance(category, dict) else ""
                    contract_time = item.get("contract_time", "")

                    text_lower = f"{title} {description}".lower()
                    is_remote = "remote" in text_lower
                    if remote == "Remote" and not is_remote:
                        continue
                    if remote == "On-site" and is_remote:
                        continue

                    jt = contract_time.replace("_", " ").title() if contract_time else ""

                    jobs.append(Job(
                        title=self._strip_html(title),
                        company=company,
                        location=loc_display,
                        description=self._clean_html(description),
                        url=job_url,
                        source=self.name,
                        remote="Remote" if is_remote else "On-site",
                        salary_min=s_min,
                        salary_max=s_max,
                        salary_currency="GBP" if country == "gb" else "USD",
                        job_type=jt,
                        date_posted=item.get("created", ""),
                        tags=cat_label,
                    ))

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
