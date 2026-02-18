"""
Ashby ATS – public job board API (no key required).
Fetches jobs from a list of company board names.
API docs: https://developers.ashbyhq.com/docs/public-job-posting-api
Endpoint: https://api.ashbyhq.com/posting-api/job-board/{board_name}
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

# Default set of well-known Ashby board names (from jobs.ashbyhq.com/<name>)
DEFAULT_BOARDS = [
    # AI & ML
    "Anthropic", "Perplexity", "Stability", "Cohere",
    "Character", "ElevenLabs",
    # Dev tools & infra
    "Linear", "Vercel", "Railway", "Fly", "Resend",
    "Neon", "Turso", "Convex", "Inngest",
    # Fintech
    "Ramp", "Brex", "Mercury",
    # Security
    "Wiz", "Huntress", "Materialize",
    # Data
    "Fivetran", "Census", "Hightouch", "Hex",
    # Health
    "Alma", "Spring Health", "Headway",
    # HR & people
    "Deel", "Ashby", "Gusto", "Rippling",
    # E-commerce
    "Faire", "Whatnot",
    # Product & design
    "Loom", "Pitch", "Rows",
    # Other notable
    "Anduril", "Flexport", "Verkada", "Samsara",
    "Plaid", "Retool", "Notion",
    "Figma", "GitLab",
    "Deliveroo", "Away",
    "FerretDB", "FlockSafety",
]


class AshbySource(BaseSource):
    name = "Ashby"
    requires_api_key = False
    base_url = "https://api.ashbyhq.com/posting-api/job-board"

    def __init__(self) -> None:
        super().__init__()
        self._boards = self._get_board_list()

    def _get_board_list(self) -> List[str]:
        try:
            import config
            tokens = getattr(config, "ASHBY_BOARD_TOKENS", None)
            if tokens and isinstance(tokens, str):
                return [t.strip() for t in tokens.split(",") if t.strip()]
            if tokens and isinstance(tokens, list):
                return list(tokens)
        except Exception:
            pass
        return DEFAULT_BOARDS

    def _parse_employment_type(self, emp_type: str) -> str:
        """Map Ashby's employmentType to our standard types."""
        if not emp_type:
            return ""
        el = emp_type.lower()
        if "full" in el:
            return "Full-time"
        if "part" in el:
            return "Part-time"
        if "contract" in el or "freelance" in el:
            return "Contract"
        if "intern" in el:
            return "Internship"
        return emp_type

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
                resp = self._get(url, params={"includeCompensation": "true"})
                data = resp.json()
            except Exception as exc:
                logger.debug("[%s] Skip board %s: %s", self.name, board, exc)
                continue

            jobs_list = data.get("jobs", []) if isinstance(data, dict) else []

            batch: List[Job] = []
            for item in jobs_list:
                if len(all_jobs) + len(batch) >= max_results:
                    break

                title = item.get("title", "")
                department = item.get("department", "")
                loc_name = item.get("location", "")
                if isinstance(loc_name, dict):
                    loc_name = loc_name.get("name", "")
                emp_type = item.get("employmentType", "")

                searchable = f"{title} {board} {loc_name} {department}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                is_remote = item.get("isRemote", False) or "remote" in loc_name.lower()
                remote_status = "Remote" if is_remote else "On-site"
                if remote == "On-site" and remote_status == "Remote":
                    continue
                if remote == "Remote" and remote_status != "Remote":
                    continue

                # Compensation
                comp = item.get("compensation") or {}
                s_min = None
                s_max = None
                s_currency = ""
                if isinstance(comp, dict):
                    comp_tiers = comp.get("compensationTierSummary") or comp.get("tiers") or []
                    if isinstance(comp_tiers, list) and comp_tiers:
                        tier = comp_tiers[0] if isinstance(comp_tiers[0], dict) else {}
                        s_min = self._safe_float(tier.get("min"))
                        s_max = self._safe_float(tier.get("max"))
                        s_currency = tier.get("currency", comp.get("currency", ""))
                    else:
                        s_min = self._safe_float(comp.get("min"))
                        s_max = self._safe_float(comp.get("max"))
                        s_currency = comp.get("currency", "")

                if salary_min and s_max and s_max < salary_min:
                    continue

                job_url = item.get("jobUrl", "") or item.get("applyUrl", "")
                if not job_url:
                    posting_id = item.get("id", "")
                    if posting_id:
                        job_url = f"https://jobs.ashbyhq.com/{board}/{posting_id}"

                date_posted = item.get("publishedDate", "") or item.get("publishedAt", "")
                if date_posted and "T" in date_posted:
                    date_posted = date_posted[:10]

                batch.append(Job(
                    title=title,
                    company=board.replace("-", " ").title() if "-" in board else board,
                    location=loc_name if isinstance(loc_name, str) else "",
                    description=self._clean_html(item.get("descriptionPlain", "") or item.get("descriptionHtml", "")),
                    url=job_url,
                    source=self.name,
                    remote=remote_status,
                    salary_min=s_min,
                    salary_max=s_max,
                    salary_currency=s_currency,
                    job_type=self._parse_employment_type(emp_type),
                    date_posted=date_posted,
                    tags=", ".join(filter(None, [department, board])),
                ))

            all_jobs.extend(batch)
            if on_batch and batch:
                on_batch(batch)

        logger.info("[%s] Found %d jobs from %d boards", self.name, len(all_jobs), len(self._boards))
        return all_jobs
