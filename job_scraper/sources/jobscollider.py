"""
JobsCollider – free API, no key required.
Endpoint: https://jobscollider.com/api/search-jobs
Also has RSS at https://jobscollider.com/remote-jobs.rss
Limitations: max 2000 results, hourly updates, 24-hour delay on new posts.
Usage requirement: must credit JobsCollider as source.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

# API category slugs for filtering
CATEGORY_MAP = {
    "software": "software-development",
    "developer": "software-development",
    "engineer": "software-development",
    "data": "data",
    "devops": "devops-sysadmin",
    "sysadmin": "devops-sysadmin",
    "design": "design",
    "marketing": "marketing",
    "sales": "sales",
    "product": "product",
    "qa": "qa",
    "security": "cybersecurity",
    "cyber": "cybersecurity",
    "finance": "finance-legal",
    "legal": "finance-legal",
    "hr": "human-resources",
    "writing": "writing",
    "customer": "customer-service",
    "project": "project-management",
    "business": "business",
}


class JobsColliderSource(BaseSource):
    name = "JobsCollider"
    requires_api_key = False
    base_url = "https://jobscollider.com/api/search-jobs"

    def _guess_category(self, keywords: List[str]) -> str:
        """Try to map keywords to a JobsCollider category slug."""
        for kw in keywords:
            for word, category in CATEGORY_MAP.items():
                if word in kw.lower():
                    return category
        return ""

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
        # JobsCollider is remote-only; skip if user wants on-site
        if remote == "On-site":
            return []

        search_terms = normalize_keywords(keywords)
        seen_urls: set = set()
        all_jobs: List[Job] = []
        on_batch = kwargs.get("on_batch")

        for term in search_terms:
            if len(all_jobs) >= max_results:
                break

            params: dict = {"query": term}
            category = self._guess_category([term])
            if category:
                params["category"] = category

            try:
                resp = self._get(self.base_url, params=params)
                data = resp.json()
            except Exception as exc:
                logger.error("[%s] Failed to fetch for '%s': %s", self.name, term, exc)
                continue

            listings = data if isinstance(data, list) else data.get("jobs", []) if isinstance(data, dict) else []

            batch: List[Job] = []
            for item in listings:
                if len(all_jobs) + len(batch) >= max_results:
                    break

                title = item.get("title", "") or item.get("name", "")
                company = item.get("company", "") or item.get("companyName", "")
                job_url = item.get("url", "") or item.get("link", "")
                loc_name = item.get("location", "") or "Remote"

                if job_url in seen_urls:
                    continue
                seen_urls.add(job_url)

                searchable = f"{title} {company} {loc_name}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                s_min = self._safe_float(item.get("salary_min") or item.get("salaryMin"))
                s_max = self._safe_float(item.get("salary_max") or item.get("salaryMax"))
                s_currency = item.get("salary_currency", "") or item.get("salaryCurrency", "")
                if salary_min and s_max and s_max < salary_min:
                    continue

                date_posted = item.get("date", "") or item.get("publishedAt", "") or item.get("pubDate", "")
                if date_posted and "T" in date_posted:
                    date_posted = date_posted[:10]

                tags_raw = item.get("tags", []) or item.get("categories", [])
                tags_str = ", ".join(tags_raw) if isinstance(tags_raw, list) else str(tags_raw)

                batch.append(Job(
                    title=title,
                    company=company,
                    location=loc_name,
                    description=self._clean_html(item.get("description", "")),
                    url=job_url,
                    source=self.name,
                    remote="Remote",
                    salary_min=s_min,
                    salary_max=s_max,
                    salary_currency=s_currency,
                    job_type=item.get("type", "") or item.get("jobType", ""),
                    date_posted=date_posted,
                    tags=tags_str,
                    company_logo=item.get("logo", "") or item.get("companyLogo", ""),
                ))

            all_jobs.extend(batch)
            if on_batch and batch:
                on_batch(batch)

        logger.info("[%s] Found %d jobs", self.name, len(all_jobs))
        return all_jobs
