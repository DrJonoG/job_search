"""
The Muse – free public API, no key required.
Endpoint: https://www.themuse.com/api/public/jobs
Supports category and level filtering; keyword matching done client-side.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

# Map our experience levels to The Muse levels
_LEVEL_MAP = {
    "entry": "Entry Level",
    "mid": "Mid Level",
    "senior": "Senior Level",
    "lead": "Senior Level",
    "executive": "Senior Level",
}


class TheMuseSource(BaseSource):
    name = "The Muse"
    requires_api_key = False
    base_url = "https://www.themuse.com/api/public/jobs"

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
        jobs: List[Job] = []
        page = 0
        max_pages = 5

        params: dict = {"page": page}
        if experience_level:
            muse_level = _LEVEL_MAP.get(experience_level.lower())
            if muse_level:
                params["level"] = muse_level

        if location:
            params["location"] = location

        while len(jobs) < max_results and page < max_pages:
            params["page"] = page
            try:
                resp = self._get(self.base_url, params=params)
                payload = resp.json()
            except Exception as exc:
                logger.error("[%s] Page %d failed: %s", self.name, page, exc)
                break

            results = payload.get("results", [])
            if not results:
                break

            for item in results:
                if len(jobs) >= max_results:
                    break

                title = item.get("name", "")
                company_obj = item.get("company", {})
                company = company_obj.get("name", "") if isinstance(company_obj, dict) else str(company_obj)

                # Locations
                locations = item.get("locations", [])
                loc_names = []
                for loc in locations:
                    if isinstance(loc, dict):
                        loc_names.append(loc.get("name", ""))
                    else:
                        loc_names.append(str(loc))
                location_str = "; ".join(loc_names) if loc_names else ""

                # Check remote
                is_remote = "flexible" in location_str.lower() or "remote" in location_str.lower()
                if remote == "Remote" and not is_remote:
                    continue
                if remote == "On-site" and is_remote:
                    continue

                # Description
                description = ""
                contents = item.get("contents", "")
                if contents:
                    description = self._clean_html(contents)

                # Categories as tags
                categories = item.get("categories", [])
                cat_names = []
                for cat in categories:
                    if isinstance(cat, dict):
                        cat_names.append(cat.get("name", ""))
                    else:
                        cat_names.append(str(cat))

                # Levels
                levels = item.get("levels", [])
                level_names = []
                for lv in levels:
                    if isinstance(lv, dict):
                        level_names.append(lv.get("name", ""))

                # Keyword filter
                searchable = f"{title} {company} {description} {' '.join(cat_names)}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                refs = item.get("refs", {})
                url = refs.get("landing_page", "") if isinstance(refs, dict) else ""

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location_str,
                    description=description,
                    url=url,
                    source=self.name,
                    remote="Remote" if is_remote else "On-site",
                    experience_level=", ".join(level_names),
                    tags=", ".join(cat_names),
                    date_posted=item.get("publication_date", ""),
                ))

            page += 1

        logger.info("[%s] Found %d jobs matching criteria", self.name, len(jobs))
        return jobs
