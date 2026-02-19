"""
Jooble – massive job aggregator. Free API key required.
Register at https://jooble.org/api/about
Endpoint: POST https://jooble.org/api/{api_key}
Aggregates from thousands of job boards worldwide.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class JoobleSource(BaseSource):
    name = "Jooble"
    requires_api_key = True
    base_url = "https://jooble.org/api"

    def is_available(self) -> bool:
        return bool(config.JOOBLE_API_KEY)

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
        results_per_page = 50

        for keyword in keywords:
            jobs_before_keyword = len(jobs)

            payload = {
                "keywords": keyword,
                "page": 1,
                "resultonpage": min(results_per_page, max_results - (len(jobs) - jobs_before_keyword)),
            }

            if location:
                payload["location"] = location
            if salary_min:
                payload["salary"] = int(salary_min)

            url = f"{self.base_url}/{config.JOOBLE_API_KEY}"

            try:
                import time
                time.sleep(self.rate_limit_delay)
                resp = self.session.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.error("[%s] Search for '%s' failed: %s", self.name, keyword, exc)
                continue

            listings = data.get("jobs", [])

            for item in listings:
                if len(jobs) - jobs_before_keyword >= max_results:
                    break

                title = item.get("title", "")
                company = item.get("company", "")
                description = item.get("snippet", "")
                job_url = item.get("link", "")
                loc = item.get("location", "")
                salary = item.get("salary", "")
                updated = item.get("updated", "")
                job_type_raw = item.get("type", "")

                # Remote check
                text_lower = f"{title} {description} {loc}".lower()
                is_remote = "remote" in text_lower
                if remote == "Remote" and not is_remote:
                    continue
                if remote == "On-site" and is_remote:
                    continue

                # Parse salary string
                s_min, s_max = self._parse_salary(salary)
                if salary_min and s_max and s_max < salary_min:
                    continue

                jobs.append(Job(
                    title=self._strip_html(title),
                    company=company,
                    location=loc,
                    description=self._clean_html(description),
                    url=job_url,
                    source=self.name,
                    remote="Remote" if is_remote else "On-site",
                    salary_min=s_min,
                    salary_max=s_max,
                    job_type=job_type_raw or job_type,
                    date_posted=updated,
                ))

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs

    @staticmethod
    def _parse_salary(salary_str: str):
        """Parse salary strings like '$50,000 - $70,000' or '50k-70k'."""
        if not salary_str:
            return None, None
        import re
        numbers = re.findall(r"[\d,]+\.?\d*", salary_str.replace(",", ""))
        if not numbers:
            return None, None
        try:
            vals = [float(n) for n in numbers]
            vals = [v * 1000 if v < 1000 else v for v in vals]
            if len(vals) >= 2:
                return min(vals), max(vals)
            return vals[0], vals[0]
        except (ValueError, IndexError):
            return None, None
