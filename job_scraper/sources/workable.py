"""
Workable ATS – public widget API (no key required).
Fetches jobs from a list of company account subdomains.
Endpoint: https://apply.workable.com/api/v1/widget/accounts/{company}
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

# Default set of well-known Workable account subdomains (from apply.workable.com/<subdomain>)
DEFAULT_BOARDS = [
    # Tech & SaaS
    "commvault", "toggl", "taxjar", "hotjar", "mimecast",
    "dataiku", "getaccept", "typeform", "contentful", "algolia",
    "zapier", "automattic", "buffer", "doist",
    "trello", "pleo", "tide", "monzo",
    # AI & data
    "deepl", "defined-ai", "synthesia",
    # Security
    "detectify", "immunefi", "hackerone",
    # Health
    "docplanner", "kry-livi",
    # HR & hiring
    "recruitee", "personio", "factorial",
    # Education
    "preply", "busuu", "babbel",
    # E-commerce
    "vinted", "catawiki", "vestiaire",
    # Fintech
    "wise", "revolut", "n26", "mollie",
    # Gaming
    "paradox-interactive",
    # Remote-first
    "omnipresent", "oyster-1", "remote-3",
    # Other notable
    "bolt-6", "blablacar", "glovo", "getaround",
    "onfido", "veriff",
    "sketch", "figma-2",
    "mixpanel", "pendo",
    "sentry-2", "logdna",
]


class WorkableSource(BaseSource):
    name = "Workable"
    requires_api_key = False
    base_url = "https://apply.workable.com/api/v1/widget/accounts"

    def __init__(self) -> None:
        super().__init__()
        self._boards = self._get_board_list()

    def _get_board_list(self) -> List[str]:
        try:
            import config
            tokens = getattr(config, "WORKABLE_BOARD_TOKENS", None)
            if tokens and isinstance(tokens, str):
                return [t.strip() for t in tokens.split(",") if t.strip()]
            if tokens and isinstance(tokens, list):
                return list(tokens)
        except Exception:
            pass
        return DEFAULT_BOARDS

    def _parse_job_type(self, type_str: str) -> str:
        """Map Workable's type field to standard types."""
        if not type_str:
            return ""
        tl = type_str.lower()
        if "full" in tl:
            return "Full-time"
        if "part" in tl:
            return "Part-time"
        if "contract" in tl or "freelance" in tl or "temporary" in tl:
            return "Contract"
        if "intern" in tl:
            return "Internship"
        return type_str

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
        all_jobs: List[Job] = []
        on_batch = kwargs.get("on_batch")

        for board in self._boards:
            if len(all_jobs) >= max_results:
                break
            try:
                url = f"{self.base_url}/{board}"
                resp = self._get(url)
                data = resp.json()
            except Exception as exc:
                logger.debug("[%s] Skip board %s: %s", self.name, board, exc)
                continue

            # Workable widget returns { "jobs": [...] } or a list directly
            if isinstance(data, dict):
                jobs_list = data.get("jobs", [])
            elif isinstance(data, list):
                jobs_list = data
            else:
                continue

            batch: List[Job] = []
            for item in jobs_list:
                if len(all_jobs) + len(batch) >= max_results:
                    break

                title = item.get("title", "")
                department = item.get("department", "")
                loc_name = ""
                loc_data = item.get("location", {})
                if isinstance(loc_data, dict):
                    parts = []
                    for key in ("city", "region", "country"):
                        val = loc_data.get(key, "")
                        if val:
                            parts.append(val)
                    loc_name = ", ".join(parts) if parts else loc_data.get("location_str", "")
                elif isinstance(loc_data, str):
                    loc_name = loc_data

                searchable = f"{title} {board} {loc_name} {department}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                is_remote = item.get("telecommuting", False) or "remote" in loc_name.lower()
                remote_status = "Remote" if is_remote else "On-site"
                if remote == "On-site" and remote_status == "Remote":
                    continue
                if remote == "Remote" and remote_status != "Remote":
                    continue

                shortcode = item.get("shortcode", "") or item.get("id", "")
                job_url = item.get("url", "")
                if not job_url and shortcode:
                    job_url = f"https://apply.workable.com/{board}/j/{shortcode}/"

                date_posted = item.get("published_on", "") or item.get("created_at", "")
                if date_posted and "T" in date_posted:
                    date_posted = date_posted[:10]

                batch.append(Job(
                    title=title,
                    company=board.replace("-", " ").title(),
                    location=loc_name,
                    description=self._clean_html(item.get("description", "")),
                    url=job_url,
                    source=self.name,
                    remote=remote_status,
                    job_type=self._parse_job_type(item.get("employment_type", "") or item.get("type", "")),
                    date_posted=date_posted,
                    tags=", ".join(filter(None, [department, board])),
                ))

            all_jobs.extend(batch)
            if on_batch and batch:
                on_batch(batch)

        logger.info("[%s] Found %d jobs from %d boards", self.name, len(all_jobs), len(self._boards))
        return all_jobs
