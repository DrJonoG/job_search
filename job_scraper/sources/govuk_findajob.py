"""
GOV.UK Find a Job (DWP) – UK official job board. Scrapes search results (semantic HTML).
URL: https://findajob.dwp.gov.uk/search
"""

from __future__ import annotations

import logging
from typing import List, Optional
from urllib.parse import urlencode

from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

SEARCH_URL = "https://findajob.dwp.gov.uk/search"


class GovUKFindAJobSource(BaseSource):
    name = "GOV.UK Find a Job"
    requires_api_key = False
    base_url = "https://findajob.dwp.gov.uk"

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
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("[%s] beautifulsoup4 not installed", self.name)
            return []

        jobs: List[Job] = []
        seen_urls: set = set()
        keywords_list = normalize_keywords(keywords)

        for query in keywords_list:
            jobs_before_keyword = len(jobs)
            params = {"q": query}
            if location:
                params["loc"] = "86383"  # UK-wide; can be refined by location code
            url = f"{SEARCH_URL}?{urlencode(params)}"
            try:
                resp = self._get(url)
                soup = BeautifulSoup(resp.text, "html.parser")
            except Exception as exc:
                logger.error("[%s] Failed for '%s': %s", self.name, query, exc)
                continue

            for block in soup.select("article, [class*='SearchResult'], [class*='job-card'], .govuk-summary-card"):
                if len(jobs) - jobs_before_keyword >= max_results:
                    break
                try:
                    link = block.select_one("a[href*='/job/']")
                    if not link or not link.get("href"):
                        continue
                    href = link["href"]
                    if href.startswith("/"):
                        href = self.base_url + href
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    title = (link.get_text(strip=True) or "").replace("Save ", "").replace(" job to favourites", "").strip()
                    if not title:
                        h3 = block.select_one("h2, h3, .govuk-heading-s")
                        if h3:
                            title = h3.get_text(strip=True)

                    loc_text = ""
                    company = ""
                    for dt in block.select("dt, [class*='location'], [class*='employer']"):
                        txt = dt.get_text(strip=True).lower()
                        next_el = dt.find_next_sibling()
                        val = next_el.get_text(strip=True) if next_el else ""
                        if "location" in txt or "where" in txt:
                            loc_text = val
                        if "employer" in txt or "company" in txt or "organisation" in txt:
                            company = val

                    if not loc_text and not company:
                        p = block.select_one("p, li, .govuk-body")
                        if p:
                            loc_text = p.get_text(strip=True)[:200]

                    searchable = f"{title} {company} {loc_text}"
                    if not self._matches_keywords(searchable, keywords):
                        continue

                    jobs.append(Job(
                        title=title or "Job",
                        company=company,
                        location=loc_text,
                        description="",
                        url=href,
                        source=self.name,
                        remote="Unknown",
                        tags="UK, government",
                    ))
                except Exception:
                    continue

        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
