"""
USAJobs – requires free API key + email.
Register at https://developer.usajobs.gov/APIRequest/Index
Endpoint: https://data.usajobs.gov/api/Search
"""

from __future__ import annotations

import logging
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class USAJobsSource(BaseSource):
    name = "USAJobs"
    requires_api_key = True
    base_url = "https://data.usajobs.gov/api/Search"

    def is_available(self) -> bool:
        return bool(config.USAJOBS_API_KEY and config.USAJOBS_EMAIL)

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
        if not self.is_available():
            logger.info("[%s] Skipped – API key not configured", self.name)
            return []

        jobs: List[Job] = []
        results_per_page = 50

        for keyword in keywords:
            jobs_before_keyword = len(jobs)

            params = {
                "Keyword": keyword,
                "ResultsPerPage": results_per_page,
            }

            if location:
                params["LocationName"] = location
            if salary_min:
                params["RemunerationMinimumAmount"] = int(salary_min)

            # Remote filter
            if remote == "Remote":
                params["RemoteIndicator"] = "True"

            headers = {
                "Authorization-Key": config.USAJOBS_API_KEY,
                "User-Agent": config.USAJOBS_EMAIL,
            }

            try:
                resp = self.session.get(
                    self.base_url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                logger.error("[%s] Search for '%s' failed: %s", self.name, keyword, exc)
                continue

            search_result = payload.get("SearchResult", {})
            items = search_result.get("SearchResultItems", [])

            for entry in items:
                if len(jobs) - jobs_before_keyword >= max_results:
                    break

                item = entry.get("MatchedObjectDescriptor", {})
                title = item.get("PositionTitle", "")
                org = item.get("OrganizationName", "")
                department = item.get("DepartmentName", "")

                # Location
                position_loc = item.get("PositionLocation", [])
                loc_parts = []
                for pl in position_loc:
                    if isinstance(pl, dict):
                        loc_parts.append(pl.get("LocationName", ""))
                location_str = "; ".join(loc_parts[:3])  # cap at 3 locations

                # Description
                qual = item.get("QualificationSummary", "")
                user_area = item.get("UserArea", {})
                details = user_area.get("Details", {}) if isinstance(user_area, dict) else {}
                major_duties = details.get("MajorDuties", "") if isinstance(details, dict) else ""

                description = f"{qual} {major_duties}".strip()

                # URL
                position_uri = item.get("PositionURI", "")
                apply_uri = item.get("ApplyURI", [])
                url = apply_uri[0] if apply_uri else position_uri

                # Salary
                remuneration = item.get("PositionRemuneration", [])
                s_min_val = None
                s_max_val = None
                s_currency = "USD"
                if remuneration and isinstance(remuneration[0], dict):
                    s_min_val = self._safe_float(remuneration[0].get("MinimumRange"))
                    s_max_val = self._safe_float(remuneration[0].get("MaximumRange"))

                # Job type
                schedule = item.get("PositionSchedule", [])
                schedule_str = ""
                if schedule and isinstance(schedule[0], dict):
                    schedule_str = schedule[0].get("Name", "")

                # Remote check
                is_remote = details.get("TeleworkEligible", "False") == "True" if isinstance(details, dict) else False
                if remote == "Remote" and not is_remote:
                    continue
                if remote == "On-site" and is_remote:
                    continue

                jobs.append(Job(
                    title=title,
                    company=f"{org} – {department}" if department else org,
                    location=location_str,
                    description=self._clean_html(description),
                    url=url,
                    source=self.name,
                    remote="Remote" if is_remote else "On-site",
                    salary_min=s_min_val,
                    salary_max=s_max_val,
                    salary_currency=s_currency,
                    job_type=schedule_str,
                    date_posted=item.get("PublicationStartDate", ""),
                    tags=item.get("JobCategory", [{}])[0].get("Name", "") if item.get("JobCategory") else "",
                ))

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
