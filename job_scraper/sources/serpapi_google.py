"""
SerpAPI Google Jobs – searches Google's job aggregation engine.
Free tier: 100 searches/month.
Register at https://serpapi.com/ (free account available).

This is extremely powerful because Google Jobs aggregates listings from
Indeed, LinkedIn, Glassdoor, ZipRecruiter, and thousands of other boards.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class SerpAPIGoogleJobsSource(BaseSource):
    name = "Google Jobs"
    requires_api_key = True
    base_url = "https://serpapi.com/search.json"

    def is_available(self) -> bool:
        return bool(config.SERPAPI_KEY)

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
            logger.info("[%s] Skipped – SERPAPI_KEY not configured", self.name)
            return []

        jobs: List[Job] = []

        for keyword in keywords:
            jobs_before_keyword = len(jobs)

            # Build the query
            query = keyword
            if location:
                query += f" in {location}"
            if remote == "Remote":
                query += " remote"

            remaining = max_results - (len(jobs) - jobs_before_keyword)
            params = {
                "engine": "google_jobs",
                "q": query,
                "api_key": config.SERPAPI_KEY,
                "num": min(10, remaining),  # Google Jobs returns ~10 per page
            }

            # Chips for filtering
            chips = []
            if remote == "Remote":
                chips.append("city:Anywhere")
            if job_type:
                jt_lower = job_type.lower()
                if "full" in jt_lower:
                    chips.append("employment_type:FULLTIME")
                elif "part" in jt_lower:
                    chips.append("employment_type:PARTTIME")
                elif "contract" in jt_lower:
                    chips.append("employment_type:CONTRACTOR")
                elif "intern" in jt_lower:
                    chips.append("employment_type:INTERN")
            if chips:
                params["chips"] = ",".join(chips)

            # Paginate through results (Google Jobs uses start token)
            start = 0
            while len(jobs) - jobs_before_keyword < max_results:
                if start > 0:
                    params["start"] = start
                params["num"] = min(10, max_results - (len(jobs) - jobs_before_keyword))

                try:
                    resp = self._get(self.base_url, params=params)
                    data = resp.json()
                except Exception as exc:
                    logger.error("[%s] Search for '%s' failed: %s", self.name, keyword, exc)
                    break

                results = data.get("jobs_results", [])
                if not results:
                    break

                for item in results:
                    if len(jobs) - jobs_before_keyword >= max_results:
                        break

                    title = item.get("title", "")
                    company = item.get("company_name", "")
                    loc = item.get("location", "")
                    description = item.get("description", "")

                    # Detected extensions (remote, full-time, salary, etc.)
                    extensions = item.get("detected_extensions", {})
                    posted_at = extensions.get("posted_at", "")
                    schedule_type = extensions.get("schedule_type", "")
                    work_from_home = extensions.get("work_from_home", False)

                    salary_info = extensions.get("salary", "")
                    s_min, s_max = self._parse_salary(salary_info)

                    if salary_min and s_max and s_max < salary_min:
                        continue

                    is_remote = work_from_home or "remote" in loc.lower()
                    if remote == "Remote" and not is_remote:
                        continue
                    if remote == "On-site" and is_remote:
                        continue

                    # Apply link – Google Jobs provides multiple apply options
                    apply_options = item.get("apply_options", [])
                    apply_url = ""
                    if apply_options and isinstance(apply_options[0], dict):
                        apply_url = apply_options[0].get("link", "")
                    if not apply_url:
                        # Fallback to sharing link
                        apply_url = item.get("share_link", "")
                        if not apply_url:
                            apply_url = item.get("related_links", [{}])[0].get("link", "") if item.get("related_links") else ""

                    # Via / source
                    via = item.get("via", "")

                    # Thumbnail / logo
                    thumbnail = item.get("thumbnail", "")

                    # Highlights as tags
                    highlights = item.get("job_highlights", [])
                    tags_parts = []
                    if via:
                        tags_parts.append(via.replace("via ", ""))
                    for hl in highlights:
                        if isinstance(hl, dict):
                            tags_parts.append(hl.get("title", ""))

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=loc,
                        description=description,
                        url=apply_url,
                        source=self.name,
                        remote="Remote" if is_remote else "On-site",
                        salary_min=s_min,
                        salary_max=s_max,
                        salary_currency="USD",
                        job_type=schedule_type or job_type,
                        experience_level=experience_level,
                        date_posted=posted_at,
                        tags=", ".join(tags_parts),
                        company_logo=thumbnail,
                    ))

                # Check for next page
                serpapi_pagination = data.get("serpapi_pagination", {})
                if not serpapi_pagination.get("next"):
                    break
                start += 10

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs

    @staticmethod
    def _parse_salary(salary_str):
        """Parse salary strings from Google Jobs like '$50K–$80K a year'."""
        if not salary_str:
            return None, None
        import re
        # Find all number-like values (with optional K/k suffix)
        matches = re.findall(r"\$?([\d,]+\.?\d*)\s*[kK]?", str(salary_str))
        if not matches:
            return None, None
        try:
            vals = []
            for m in matches:
                v = float(m.replace(",", ""))
                # Detect "K" suffix in original string near this number
                if v < 1000 and ("k" in salary_str.lower()):
                    v *= 1000
                vals.append(v)
            vals = [v for v in vals if v > 0]
            if len(vals) >= 2:
                return min(vals), max(vals)
            elif vals:
                return vals[0], vals[0]
        except (ValueError, IndexError):
            pass
        return None, None
