"""
DevITjobs UK – free RSS/XML feed, no key required.
Feed: https://devitjobs.uk/job_feed.xml
Coverage: UK developer and tech jobs.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class DevITJobsSource(BaseSource):
    name = "DevITjobs"
    requires_api_key = False
    base_url = "https://devitjobs.uk/job_feed.xml"

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
        try:
            import feedparser
        except ImportError:
            logger.warning("[%s] feedparser not installed – skipping", self.name)
            return []

        try:
            resp = self._get(self.base_url)
            feed = feedparser.parse(resp.content)
        except Exception as exc:
            logger.error("[%s] Failed to fetch RSS: %s", self.name, exc)
            return []

        all_jobs: List[Job] = []
        on_batch = kwargs.get("on_batch")

        for entry in feed.entries:
            if len(all_jobs) >= max_results:
                break

            title = entry.get("title", "")
            company = entry.get("author", "") or entry.get("dc_creator", "")
            link = entry.get("link", "")
            description = entry.get("summary", "") or entry.get("description", "")
            loc_name = entry.get("location", "") or "United Kingdom"

            # Try to extract location from categories/tags
            tags_list = []
            if hasattr(entry, "tags"):
                for tag in entry.tags:
                    term = tag.get("term", "")
                    if term:
                        tags_list.append(term)

            searchable = f"{title} {company} {description} {' '.join(tags_list)}"
            if not self._matches_keywords(searchable, keywords):
                continue

            is_remote = "remote" in searchable.lower()
            remote_status = "Remote" if is_remote else "On-site"
            if remote == "On-site" and remote_status == "Remote":
                continue
            if remote == "Remote" and remote_status != "Remote":
                continue

            # Parse salary from description or title if available
            s_min = None
            s_max = None
            s_currency = "GBP"
            salary_text = f"{title} {description}"
            try:
                import re
                salary_match = re.search(r'[£$€]\s*([\d,]+)\s*[-–to]+\s*[£$€]?\s*([\d,]+)', salary_text)
                if salary_match:
                    s_min = self._safe_float(salary_match.group(1).replace(",", ""))
                    s_max = self._safe_float(salary_match.group(2).replace(",", ""))
                    if "€" in salary_text:
                        s_currency = "EUR"
                    elif "$" in salary_text:
                        s_currency = "USD"
            except Exception:
                pass

            if salary_min and s_max and s_max < salary_min:
                continue

            date_posted = ""
            if hasattr(entry, "published"):
                date_posted = entry.published
            elif hasattr(entry, "updated"):
                date_posted = entry.updated
            if date_posted:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_posted)
                    date_posted = dt.strftime("%Y-%m-%d")
                except Exception:
                    if "T" in date_posted:
                        date_posted = date_posted[:10]

            all_jobs.append(Job(
                title=title,
                company=company,
                location=loc_name,
                description=self._clean_html(description),
                url=link,
                source=self.name,
                remote=remote_status,
                salary_min=s_min,
                salary_max=s_max,
                salary_currency=s_currency if (s_min or s_max) else "",
                date_posted=date_posted,
                tags=", ".join(tags_list),
            ))

        if on_batch and all_jobs:
            on_batch(all_jobs)

        logger.info("[%s] Found %d jobs from RSS feed", self.name, len(all_jobs))
        return all_jobs
