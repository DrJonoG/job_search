"""
Lever ATS – public Postings API (no key required).
Fetches jobs from a list of company slugs via the Lever Postings API.
API docs: https://github.com/lever/postings-api
Endpoint: https://api.lever.co/v0/postings/{company}?mode=json
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

# Default set of well-known Lever company slugs (from jobs.lever.co/<slug>)
DEFAULT_BOARDS = [
    # Big tech & enterprise
    "netflix", "atlassian", "shopify", "twitch",
    # Fintech & payments
    "ampla", "clearco", "nuvei", "payoneer", "plaid",
    # Dev tools & infra
    "grafana", "postman", "snyk", "sentry", "supabase", "render",
    "sourcegraph", "temporal", "hasura", "prisma",
    # AI & ML
    "openai", "cohere", "weights-and-biases", "jasper",
    "replicate", "huggingface",
    # Cloud & security
    "tailscale", "teleport", "lacework", "orca-security",
    # Product & design
    "canva", "miro", "notion", "coda",
    # E-commerce & marketplace
    "faire", "whatnot", "goat", "poshmark",
    # HR & people
    "deel", "oysterhr", "remotecom", "lattice", "culture-amp",
    # Health & biotech
    "tempus", "color", "ro", "alma", "hims",
    # Fintech
    "ramp", "brex", "mercury", "moderntreasury",
    # Data & analytics
    "dbt-labs", "preset", "metabase", "monte-carlo-data",
    # Comms & social
    "loom", "calendly", "bereal",
    # Other notable
    "anduril", "flexport", "abridge", "applied-intuition",
    "cruise", "nuro", "zipline", "relativity",
    "benchling", "samsara", "verkada",
    "lucidmotors", "rivian",
    "wealthsimple", "robinhood",
]


class LeverSource(BaseSource):
    name = "Lever"
    requires_api_key = False
    base_url = "https://api.lever.co/v0/postings"

    def __init__(self) -> None:
        super().__init__()
        self._boards = self._get_board_list()

    def _get_board_list(self) -> List[str]:
        try:
            import config
            tokens = getattr(config, "LEVER_BOARD_TOKENS", None)
            if tokens and isinstance(tokens, str):
                return [t.strip() for t in tokens.split(",") if t.strip()]
            if tokens and isinstance(tokens, list):
                return list(tokens)
        except Exception:
            pass
        return DEFAULT_BOARDS

    def _parse_remote(self, item: dict, loc_name: str) -> str:
        """Determine remote status from Lever's workplaceType field or location."""
        workplace = item.get("workplaceType", "unspecified")
        if workplace == "remote":
            return "Remote"
        if workplace == "hybrid":
            return "Hybrid"
        if workplace == "on-site":
            return "On-site"
        if "remote" in loc_name.lower():
            return "Remote"
        return "Unknown"

    def _parse_job_type(self, item: dict) -> str:
        """Extract job type from Lever's categories.commitment field."""
        cats = item.get("categories", {})
        if not isinstance(cats, dict):
            return ""
        commitment = cats.get("commitment", "")
        if not commitment:
            return ""
        cl = commitment.lower()
        if "full" in cl:
            return "Full-time"
        if "part" in cl:
            return "Part-time"
        if "contract" in cl or "freelance" in cl:
            return "Contract"
        if "intern" in cl:
            return "Internship"
        return commitment

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
                url = f"{self.base_url}/{board}?mode=json"
                resp = self._get(url)
                data = resp.json()
            except Exception as exc:
                logger.debug("[%s] Skip board %s: %s", self.name, board, exc)
                continue

            if not isinstance(data, list):
                continue

            batch: List[Job] = []
            for item in data:
                if len(all_jobs) + len(batch) >= max_results:
                    break

                title = item.get("text", "")
                cats = item.get("categories", {}) if isinstance(item.get("categories"), dict) else {}
                loc_name = cats.get("location", "") or cats.get("allLocations", "")
                if isinstance(loc_name, list):
                    loc_name = ", ".join(loc_name)
                team = cats.get("team", "")
                department = cats.get("department", "")

                searchable = f"{title} {board} {loc_name} {team} {department}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                remote_status = self._parse_remote(item, loc_name)
                if remote == "On-site" and remote_status == "Remote":
                    continue
                if remote == "Remote" and remote_status not in ("Remote", "Unknown"):
                    continue

                # Salary
                salary_range = item.get("salaryRange") or {}
                s_min = self._safe_float(salary_range.get("min"))
                s_max = self._safe_float(salary_range.get("max"))
                s_currency = salary_range.get("currency", "")
                if salary_min and s_max and s_max < salary_min:
                    continue

                job_url = item.get("hostedUrl", "")
                created_at = item.get("createdAt")
                date_posted = ""
                if created_at:
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
                        date_posted = dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass

                batch.append(Job(
                    title=title,
                    company=board.replace("-", " ").title(),
                    location=loc_name,
                    description=self._clean_html(item.get("descriptionPlain", "") or item.get("description", "")),
                    url=job_url,
                    source=self.name,
                    remote=remote_status,
                    salary_min=s_min,
                    salary_max=s_max,
                    salary_currency=s_currency,
                    job_type=self._parse_job_type(item),
                    date_posted=date_posted,
                    tags=", ".join(filter(None, [team, department, board])),
                ))

            all_jobs.extend(batch)
            if on_batch and batch:
                on_batch(batch)

        logger.info("[%s] Found %d jobs from %d boards", self.name, len(all_jobs), len(self._boards))
        return all_jobs
