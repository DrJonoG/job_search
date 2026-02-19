"""
LinkedIn – jobs from LinkedIn via the python-jobspy scraper.

LinkedIn does not offer a free public job-search API (see linkedin.txt).
This source uses the same scraping pipeline as JobSpy but restricted to
LinkedIn only, so you can select "LinkedIn" as a single source.

Requires: pip install python-jobspy
No API key. If python-jobspy is not installed, this source is unavailable.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .jobspy_source import JobSpySource

logger = logging.getLogger(__name__)


class LinkedInSource(JobSpySource):
    """
    Fetches job listings from LinkedIn only, using the JobSpy scraper.
    Same behaviour as JobSpy but with site_name=["linkedin"].
    """

    name = "LinkedIn"
    requires_api_key = False
    base_url = "https://www.linkedin.com/jobs/"

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
            logger.info("[%s] Skipped – python-jobspy not installed", self.name)
            return []

        jobs = super().fetch_jobs(
            keywords=keywords,
            location=location,
            remote=remote,
            job_type=job_type,
            salary_min=salary_min,
            experience_level=experience_level,
            max_results=max_results,
            posted_in_last_days=posted_in_last_days,
            sites=["linkedin"],
            country=None,  # use config.JOBSPY_COUNTRIES (same as JobSpy)
        )
        for job in jobs:
            job.source = self.name
        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
