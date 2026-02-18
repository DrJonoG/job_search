"""
We Work Remotely – free, no key required.
Parses RSS feeds from weworkremotely.com.
One of the most popular remote job boards.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

# RSS feed URLs by category
_FEEDS = {
    "programming": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "design": "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "devops": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "management": "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
    "customer_support": "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "sales_marketing": "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
    "all_others": "https://weworkremotely.com/categories/remote-jobs.rss",
}


class WeWorkRemotelySource(BaseSource):
    name = "WeWorkRemotely"
    requires_api_key = False
    base_url = "https://weworkremotely.com"

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
        # WWR is remote-only
        if remote == "On-site":
            return []

        try:
            import feedparser
        except ImportError:
            logger.warning("[%s] feedparser not installed – pip install feedparser", self.name)
            return []

        jobs: List[Job] = []

        # Determine which feeds to scrape based on keywords
        feeds_to_check = self._select_feeds(keywords)

        for feed_name, feed_url in feeds_to_check.items():
            if len(jobs) >= max_results:
                break

            try:
                feed = feedparser.parse(feed_url)
            except Exception as exc:
                logger.error("[%s] Failed to parse %s: %s", self.name, feed_name, exc)
                continue

            for entry in feed.entries:
                if len(jobs) >= max_results:
                    break

                title = entry.get("title", "")
                link = entry.get("link", "")

                # WWR titles often include company: "Company Name: Job Title"
                company = ""
                clean_title = title
                if ":" in title:
                    parts = title.split(":", 1)
                    company = parts[0].strip()
                    clean_title = parts[1].strip()

                description = entry.get("summary", "") or entry.get("description", "")
                pub_date = entry.get("published", "")

                # Keyword filter
                searchable = f"{title} {description} {feed_name}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                # Tags from categories
                tags = []
                for tag in entry.get("tags", []):
                    if isinstance(tag, dict):
                        tags.append(tag.get("term", ""))
                    else:
                        tags.append(str(tag))

                jobs.append(Job(
                    title=clean_title,
                    company=company,
                    location="Remote",
                    description=self._clean_html(description),
                    url=link,
                    source=self.name,
                    remote="Remote",
                    job_type=job_type or "Full-time",
                    date_posted=pub_date,
                    tags=", ".join(tags) if tags else feed_name,
                ))

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs

    @staticmethod
    def _select_feeds(keywords: List[str]) -> dict:
        """Pick relevant RSS feeds based on keyword content."""
        if not keywords:
            return _FEEDS

        selected = {}
        kw_combined = " ".join(keywords).lower()

        keyword_to_feed = {
            "programming": ["developer", "engineer", "software", "python", "java", "react", "backend", "frontend", "full stack", "web dev", "mobile"],
            "design": ["design", "ux", "ui", "graphic", "creative"],
            "devops": ["devops", "sysadmin", "infrastructure", "cloud", "aws", "azure", "kubernetes"],
            "management": ["manager", "management", "finance", "accounting", "project"],
            "customer_support": ["customer", "support", "service"],
            "sales_marketing": ["sales", "marketing", "growth", "seo", "content"],
        }

        for feed_key, triggers in keyword_to_feed.items():
            if any(t in kw_combined for t in triggers):
                selected[feed_key] = _FEEDS[feed_key]

        # Always include all_others as a fallback, and programming as a default
        if not selected:
            selected = {"programming": _FEEDS["programming"], "all_others": _FEEDS["all_others"]}

        return selected
