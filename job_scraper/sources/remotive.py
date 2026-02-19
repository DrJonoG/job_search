"""
Remotive – free API, no key required.
Endpoint: https://remotive.com/api/remote-jobs
Excellent source for remote tech/non-tech jobs worldwide.
Supports category and search filtering.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

# Remotive categories for smarter querying
_CATEGORY_MAP = {
    "software": "software-dev",
    "engineer": "software-dev",
    "developer": "software-dev",
    "data": "data",
    "analyst": "data",
    "machine learning": "data",
    "design": "design",
    "marketing": "marketing",
    "product": "product",
    "customer": "customer-support",
    "sales": "sales",
    "devops": "devops-sysadmin",
    "finance": "finance-legal",
    "hr": "hr",
    "writing": "writing",
    "qa": "qa",
}


class RemotiveSource(BaseSource):
    name = "Remotive"
    requires_api_key = False
    base_url = "https://remotive.com/api/remote-jobs"

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
        # Remotive is remote-only
        if remote == "On-site":
            return []

        jobs: List[Job] = []
        keywords_list = normalize_keywords(keywords, default=[""])
        if not keywords_list or keywords_list == [""]:
            keywords_list = [""]

        for keyword in keywords_list:
            jobs_before_keyword = len(jobs)

            # Try to match a category from this keyword
            category = ""
            if keyword:
                kw_lower = keyword.lower().strip()
                for trigger, cat in _CATEGORY_MAP.items():
                    if trigger in kw_lower:
                        category = cat
                        break

            params: dict = {"limit": min(max_results, 1000)}
            if category:
                params["category"] = category
            if keyword:
                params["search"] = keyword

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

            title = item.get("title", "")
            company = item.get("company_name", "")
            description = item.get("description", "")
            tags = item.get("tags", [])
            tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
            candidate_location = item.get("candidate_required_location", "Worldwide")
            job_url = item.get("url", "")

            # Salary parsing
            salary_raw = item.get("salary", "")
            s_min, s_max = self._parse_salary_string(salary_raw)

            if salary_min and s_max and s_max < salary_min:
                continue

            # Keyword filter (beyond the API's search)
            searchable = f"{title} {company} {description} {tags_str}"
            if not self._matches_keywords(searchable, keywords):
                continue

            jt = item.get("job_type", "")

            jobs.append(Job(
                title=title,
                company=company,
                location=candidate_location,
                description=self._clean_html(description),
                url=job_url,
                source=self.name,
                remote="Remote",
                salary_min=s_min,
                salary_max=s_max,
                salary_currency="USD",
                job_type=jt.replace("_", " ").title() if jt else "",
                date_posted=item.get("publication_date", ""),
                tags=tags_str,
                company_logo=item.get("company_logo", ""),
            ))

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs

    @staticmethod
    def _parse_salary_string(salary_str: str):
        """Try to extract min/max from strings like '$60,000 - $90,000' or '60k-90k'."""
        if not salary_str:
            return None, None
        import re
        numbers = re.findall(r"[\d,]+\.?\d*", salary_str.replace(",", ""))
        if not numbers:
            return None, None
        try:
            vals = [float(n) for n in numbers]
            # If values look like they're in thousands (e.g. 60, 90)
            vals = [v * 1000 if v < 1000 else v for v in vals]
            if len(vals) >= 2:
                return min(vals), max(vals)
            return vals[0], vals[0]
        except (ValueError, IndexError):
            return None, None
