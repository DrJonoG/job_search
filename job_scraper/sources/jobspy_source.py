"""
JobSpy – wraps the python-jobspy library for scraping major job boards.
pip install python-jobspy

Supported sites: Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google, Bayt (Middle East), Naukri (India), BDJobs (Bangladesh).
No API keys required – uses web scraping under the hood.

NOTE: This is optional. If python-jobspy is not installed, this source
is silently unavailable. Install with: pip install python-jobspy
"""

from __future__ import annotations

import logging
import random
import time
from typing import List, Optional, Set

import config
from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

# Rotating user agents to reduce fingerprinting and 429/CAPTCHA blocks from Google
_JOBSPY_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Try to import jobspy at module level so we can check availability
_JOBSPY_AVAILABLE = False
try:
    from jobspy import scrape_jobs as _scrape_jobs
    _JOBSPY_AVAILABLE = True
except ImportError:
    _scrape_jobs = None


# Default sites if config not set (config.JOBSPY_SITES is the source of truth)
_DEFAULT_JOBSPY_SITES = ["indeed", "linkedin", "glassdoor", "zip_recruiter", "google", "bayt", "naukri", "bdjobs"]


class JobSpySource(BaseSource):
    """
    Aggregates jobs from Indeed, LinkedIn, Glassdoor, ZipRecruiter, and
    Google via the python-jobspy scraping library.
    """

    name = "JobSpy"
    requires_api_key = False
    base_url = ""

    def is_available(self) -> bool:
        return _JOBSPY_AVAILABLE

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
        sites: Optional[List[str]] = None,
        country: Optional[str] = None,
    ) -> List[Job]:
        if not self.is_available():
            logger.info("[%s] Skipped – python-jobspy not installed", self.name)
            return []

        # Countries to search (Indeed/Glassdoor are per-country). From config or single override.
        countries_to_use: List[str] = [country] if country else getattr(config, "JOBSPY_COUNTRIES", ["USA"])
        if not countries_to_use:
            countries_to_use = ["USA"]

        all_jobs: List[Job] = []
        seen_urls: Set[str] = set()
        sites_to_use = sites or getattr(config, "JOBSPY_SITES", _DEFAULT_JOBSPY_SITES) or _DEFAULT_JOBSPY_SITES
        results_per_keyword_per_country = max(5, max_results // max(1, len(countries_to_use)))
        delay_sec = getattr(config, "JOBSPY_DELAY_BETWEEN_REQUESTS", 8.0)
        first_request = True

        for keyword in keywords:
            jobs_before_keyword = len(all_jobs)

            for country_code in countries_to_use:
                if len(all_jobs) - jobs_before_keyword >= max_results:
                    break

                if not first_request and delay_sec > 0:
                    logger.info("[%s] Rate limit delay %.1fs before next request ...", self.name, delay_sec)
                    time.sleep(delay_sec)
                first_request = False

                remaining = max_results - (len(all_jobs) - jobs_before_keyword)
                scrape_kwargs = {
                    "site_name": sites_to_use,
                    "search_term": keyword,
                    "results_wanted": min(results_per_keyword_per_country, remaining),
                    "country_indeed": country_code.strip(),
                    "verbose": 0,
                    "user_agent": random.choice(_JOBSPY_USER_AGENTS),
                }

                if location:
                    scrape_kwargs["location"] = location

                if remote == "Remote":
                    scrape_kwargs["is_remote"] = True

                # Hours old – limit to jobs posted in last N days (JobSpy uses hours)
                if posted_in_last_days and posted_in_last_days > 0:
                    scrape_kwargs["hours_old"] = min(posted_in_last_days * 24, 720)  # cap 30 days
                else:
                    scrape_kwargs["hours_old"] = 72  # default 3 days

                # Job type mapping for jobspy
                if job_type:
                    jt_lower = job_type.lower()
                    if "full" in jt_lower:
                        scrape_kwargs["job_type"] = "fulltime"
                    elif "part" in jt_lower:
                        scrape_kwargs["job_type"] = "parttime"
                    elif "contract" in jt_lower:
                        scrape_kwargs["job_type"] = "contract"
                    elif "intern" in jt_lower:
                        scrape_kwargs["job_type"] = "internship"

                try:
                    logger.info("[%s] Scraping %s for '%s' (%s) ...", self.name, sites_to_use, keyword, country_code)
                    df = _scrape_jobs(**scrape_kwargs)

                    if df is None or df.empty:
                        continue

                    for _, row in df.iterrows():
                        if len(all_jobs) - jobs_before_keyword >= max_results:
                            break

                        job_url = str(row.get("job_url", "") or row.get("job_url_direct", ""))
                        if job_url and job_url in seen_urls:
                            continue
                        if job_url:
                            seen_urls.add(job_url)

                        title = str(row.get("title", ""))
                        company = str(row.get("company_name", "") or row.get("company", ""))
                        loc = str(row.get("location", ""))
                        description = str(row.get("description", ""))
                        site = str(row.get("site", ""))
                        date_posted = str(row.get("date_posted", ""))

                        is_remote = bool(row.get("is_remote", False))
                        if remote == "Remote" and not is_remote and "remote" not in loc.lower():
                            continue
                        if remote == "On-site" and (is_remote or "remote" in loc.lower()):
                            continue

                        # Salary
                        s_min = self._safe_float(row.get("min_amount"))
                        s_max = self._safe_float(row.get("max_amount"))
                        s_currency = str(row.get("currency", "USD") or "USD")

                        if salary_min and s_max and s_max < salary_min:
                            continue

                        jt = str(row.get("job_type", "")) or job_type
                        logo = str(row.get("company_logo", "") or row.get("logo_photo_url", "") or "")

                        all_jobs.append(Job(
                            title=title,
                            company=company,
                            location=loc,
                            description=self._clean_html(description)[:5000],  # cap description length
                            url=job_url,
                            source=f"JobSpy ({site.title()})" if site else self.name,
                            remote="Remote" if is_remote else "On-site",
                            salary_min=s_min,
                            salary_max=s_max,
                            salary_currency=s_currency,
                            job_type=jt.replace("_", " ").title() if jt else "",
                            date_posted=date_posted,
                            company_logo=logo,
                        ))

                except Exception as exc:
                    logger.error("[%s] Scrape for '%s' (%s) failed: %s", self.name, keyword, country_code, exc)
                    continue

        logger.info("[%s] Found %d jobs from scraped sources", self.name, len(all_jobs))
        return all_jobs
