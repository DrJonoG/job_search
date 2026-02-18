"""
Remote.co – remote jobs (free). Fetches job listings via their public jobs page.
Uses simple HTTP + BeautifulSoup; no API key.
"""

from __future__ import annotations

import logging
from typing import List, Optional
from urllib.parse import urlencode

from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

SEARCH_URL = "https://remote.co/remote-jobs/search/"


class RemoteCoSource(BaseSource):
    name = "Remote.co"
    requires_api_key = False
    base_url = "https://remote.co"

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
        if remote == "On-site":
            return []

        jobs: List[Job] = []
        seen_urls: set = set()
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("[%s] beautifulsoup4 not installed", self.name)
            return []

        keywords_list = normalize_keywords(keywords)

        for keyword in keywords_list:
            jobs_before_keyword = len(jobs)
            url = f"{SEARCH_URL}?{urlencode({'search_keywords': keyword})}"
            try:
                resp = self._get(url)
                soup = BeautifulSoup(resp.text, "html.parser")
            except Exception as exc:
                logger.error("[%s] Failed for '%s': %s", self.name, keyword, exc)
                continue

            for card in soup.select(".job_listing, .job-listing, article.job, .job-listings .job, [class*='job-card']"):
                if len(jobs) - jobs_before_keyword >= max_results:
                    break
                try:
                    link_el = card.select_one("a[href*='/job/'], a[href*='remote.co']")
                    title_el = card.select_one("h2, h3, .title, .job-title, [class*='title']")
                    company_el = card.select_one(".company, .employer, [class*='company']")
                    desc_el = card.select_one(".description, .excerpt, [class*='description']")

                    title = (title_el.get_text(strip=True) if title_el else "") or (link_el.get_text(strip=True) if link_el else "")
                    company = company_el.get_text(strip=True) if company_el else ""
                    description = desc_el.get_text(strip=True) if desc_el else ""
                    job_url = ""
                    if link_el and link_el.get("href"):
                        job_url = link_el["href"]
                        if job_url.startswith("/"):
                            job_url = self.base_url + job_url

                    if not title and not job_url:
                        continue
                    if job_url and job_url in seen_urls:
                        continue
                    if job_url:
                        seen_urls.add(job_url)

                    searchable = f"{title} {company} {description}"
                    if not self._matches_keywords(searchable, keywords):
                        continue

                    jobs.append(Job(
                        title=title or "Remote job",
                        company=company,
                        location="Remote",
                        description=description,
                        url=job_url or self.base_url,
                        source=self.name,
                        remote="Remote",
                        tags="",
                    ))
                except Exception:
                    continue

        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
