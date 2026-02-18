"""
Totaljobs – UK job board via RSS (free, no key).
RSS feed: https://www.totaljobs.com/JobSearch/RSSLink.aspx
Query params may be supported for keywords/location (site-dependent).
"""

from __future__ import annotations

import logging
from typing import List, Optional
from urllib.parse import urlencode

from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

RSS_BASE = "https://www.totaljobs.com/JobSearch/RSSLink.aspx"


class TotaljobsSource(BaseSource):
    name = "Totaljobs"
    requires_api_key = False
    base_url = "https://www.totaljobs.com"

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
            import feedparser
        except ImportError:
            logger.warning("[%s] feedparser not installed", self.name)
            return []

        jobs: List[Job] = []
        seen_links: set = set()
        keywords_list = normalize_keywords(keywords)

        for keyword in keywords_list:
            jobs_before_keyword = len(jobs)
            params = {"keywords": keyword}
            if location:
                params["location"] = location
            url = f"{RSS_BASE}?{urlencode(params)}"

            try:
                feed = feedparser.parse(url)
            except Exception as exc:
                logger.error("[%s] Failed for '%s': %s", self.name, keyword, exc)
                continue

            for entry in feed.entries:
                if len(jobs) - jobs_before_keyword >= max_results:
                    break
                link = entry.get("link", "")
                if link and link in seen_links:
                    continue
                if link:
                    seen_links.add(link)

                title = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                published = entry.get("published", "")

                searchable = f"{title} {summary}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                jobs.append(Job(
                    title=title,
                    company=entry.get("author", ""),
                    location=location or "",
                    description=self._clean_html(summary),
                    url=link,
                    source=self.name,
                    remote="Unknown",
                    date_posted=published,
                    tags="",
                ))

        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
