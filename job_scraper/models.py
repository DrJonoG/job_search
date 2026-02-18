"""
Data models for job listings.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Job:
    """Represents a single job listing."""

    title: str
    company: str
    location: str
    description: str
    url: str
    source: str

    # Optional enriched fields
    remote: str = "Unknown"                     # Remote / On-site / Hybrid / Unknown
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = ""
    job_type: str = ""                          # Full-time, Part-time, Contract …
    experience_level: str = ""
    date_posted: str = ""
    tags: str = ""
    company_logo: str = ""

    # Auto-populated
    date_scraped: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    job_id: str = ""

    def __post_init__(self) -> None:
        """Generate a deterministic unique ID if not already set."""
        if not self.job_id:
            self.job_id = self._generate_id()
        # Light trim on description (preserve HTML structure and line breaks)
        if self.description:
            self.description = self.description.strip()

    # ── helpers ────────────────────────────────────────────────
    def _generate_id(self) -> str:
        """Create a stable hash from source + url (or source + title + company)."""
        raw = f"{self.source}|{self.url}" if self.url else f"{self.source}|{self.title}|{self.company}"
        return hashlib.md5(raw.encode()).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def csv_columns() -> list[str]:
        """Ordered column list for the CSV."""
        return [
            "job_id",
            "title",
            "company",
            "location",
            "description",
            "url",
            "source",
            "remote",
            "salary_min",
            "salary_max",
            "salary_currency",
            "job_type",
            "experience_level",
            "date_posted",
            "date_scraped",
            "tags",
            "company_logo",
        ]
