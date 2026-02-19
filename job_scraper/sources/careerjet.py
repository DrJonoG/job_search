"""
CareerJet – free public API with affiliate ID (register at careerjet.com/partners/api).
Aggregates jobs from many boards; 1000 calls/hour on free tier.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

API_URL = "http://public.api.careerjet.net/search"


class CareerJetSource(BaseSource):
    name = "CareerJet"
    requires_api_key = True
    base_url = "https://www.careerjet.com"

    def is_available(self) -> bool:
        return bool(getattr(config, "CAREERJET_AFFID", "") or getattr(config, "CAREERJET_API_KEY", ""))

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
        affid = getattr(config, "CAREERJET_AFFID", "") or getattr(config, "CAREERJET_API_KEY", "")
        if not affid:
            return []

        jobs: List[Job] = []
        seen_urls: set = set()
        keywords_list = normalize_keywords(keywords)
        page_size = 100
        max_pages_per_keyword = max(1, (max_results + page_size - 1) // page_size)

        for keyword in keywords_list:
            jobs_before_keyword = len(jobs)
            page = 1
            while len(jobs) - jobs_before_keyword < max_results and page <= max_pages_per_keyword:
                remaining = max_results - (len(jobs) - jobs_before_keyword)
                params = {
                    "locale_code": "en_GB",
                    "keywords": keyword,
                    "affid": affid,
                    "format": "json",
                    "pagesize": min(page_size, remaining),
                    "page": page,
                }
                if location:
                    params["location"] = location

                try:
                    resp = self._get(API_URL, params=params)
                    data = resp.json()
                except Exception as exc:
                    logger.error("[%s] Failed for '%s': %s", self.name, keyword, exc)
                    break

                hits = data.get("hits", []) if isinstance(data, dict) else []
                if not hits:
                    break

                for item in hits:
                    if len(jobs) - jobs_before_keyword >= max_results:
                        break
                    url = item.get("url", "")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)

                    title = item.get("title", "")
                    company = item.get("company", "")
                    locations = item.get("locations", "")
                    if isinstance(locations, list):
                        locations = ", ".join(str(x) for x in locations)
                    description = item.get("description", "") or item.get("snippet", "")

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=locations,
                        description=self._clean_html(description),
                        url=url,
                        source=self.name,
                        remote="Remote" if "remote" in (locations or "").lower() else "Unknown",
                        date_posted=item.get("date", ""),
                        tags="",
                    ))
                page += 1

        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
