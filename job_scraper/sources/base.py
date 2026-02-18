"""
Abstract base class for all job source adapters.
Every source must implement `fetch_jobs()`.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import requests

from ..models import Job
import config

logger = logging.getLogger(__name__)


def normalize_keywords(keywords: List[str], default: Optional[List[str]] = None) -> List[str]:
    """
    Return a list of non-empty stripped keywords for searching.
    If the result would be empty, returns default or ['job'].
    Use this so every source searches one term at a time instead of a concatenated string.
    """
    result = [kw.strip() for kw in keywords if kw and kw.strip()]
    return result if result else (default if default is not None else ["job"])


class BaseSource(ABC):
    """Interface that every job source adapter must follow."""

    name: str = "BaseSource"
    requires_api_key: bool = False
    base_url: str = ""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "JobSearchTool/1.0 (github.com/jobsearch)",
            "Accept": "application/json",
        })
        self.timeout = config.REQUEST_TIMEOUT
        self.rate_limit_delay = config.RATE_LIMIT_DELAY
        self.max_results = config.MAX_RESULTS_PER_SOURCE

    @abstractmethod
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
        """
        Fetch jobs from this source matching the given criteria.
        Must return a list of Job objects.
        Optional kwargs: on_batch(batch: List[Job]) – if provided, call after each batch (e.g. per search); caller may save to DB.
        When filtering by salary_min: only exclude jobs whose *known* salary max is below
        the user's minimum; jobs with unknown/missing salary must be included.
        """
        ...

    def is_available(self) -> bool:
        """
        Check whether this source can be used (e.g. API keys present).
        Override in subclasses that require keys.
        """
        return True

    # ── helpers ────────────────────────────────────────────────
    def _get(self, url: str, params: Optional[Dict] = None, **kwargs) -> requests.Response:
        """Perform a rate-limited GET request with error handling."""
        time.sleep(self.rate_limit_delay)
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.debug("[%s] Request failed: %s – %s", self.name, url, exc)
            raise

    def _matches_keywords(self, text: str, keywords: List[str]) -> bool:
        """
        Check if any keyword (or a meaningful part of it) appears in the text.
        Matches when:
        - The full keyword phrase is in the text, or
        - Any multi-word prefix of the keyword is in the text
          (e.g. 'machine learning' matches search 'machine learning engineer').
        Case-insensitive.
        """
        if not keywords:
            return True
        text_lower = text.lower()

        for kw in keywords:
            kw_clean = kw.strip()
            if not kw_clean:
                continue
            kw_lower = kw_clean.lower()
            # Full phrase match
            if kw_lower in text_lower:
                return True
            # Match if any multi-word prefix of the keyword is in the text
            # e.g. "machine learning" in job matches search "machine learning engineer"
            words = kw_lower.split()
            for n in range(2, len(words) + 1):  # at least 2 words to avoid single-word noise
                phrase = " ".join(words[:n])
                if phrase in text_lower:
                    return True
        return False

    @staticmethod
    def _clean_html(html: str) -> str:
        """
        Sanitize HTML: keep safe structural tags for readable descriptions,
        remove dangerous elements (script, style, iframe, form, input).
        Falls back to plain-text extraction if BeautifulSoup is unavailable.
        """
        if not html:
            return ""
        try:
            from bs4 import BeautifulSoup, Comment
            soup = BeautifulSoup(html, "html.parser")

            # Remove dangerous elements entirely
            for tag in soup.find_all(["script", "style", "iframe", "form",
                                      "input", "button", "textarea", "select",
                                      "object", "embed", "applet", "noscript"]):
                tag.decompose()

            # Remove HTML comments
            for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
                comment.extract()

            # Remove all attributes except href on <a> and src on <img>
            safe_attrs = {
                "a": ["href"],
                "img": ["src", "alt"],
            }
            for tag in soup.find_all(True):
                allowed = safe_attrs.get(tag.name, [])
                attrs = dict(tag.attrs)
                for attr in attrs:
                    if attr not in allowed:
                        del tag[attr]
                # Ensure links open in new tab
                if tag.name == "a" and tag.get("href"):
                    tag["target"] = "_blank"
                    tag["rel"] = "noopener noreferrer"

            result = str(soup).strip()
            # If the result has no HTML tags at all, it's plain text – return as-is
            return result if result else ""
        except Exception:
            import re
            return re.sub(r"<[^>]+>", " ", html).strip()

    @staticmethod
    def _strip_html(html: str) -> str:
        """Strip ALL HTML tags from a string, returning plain text."""
        if not html:
            return ""
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
        except Exception:
            import re
            return re.sub(r"<[^>]+>", " ", html).strip()

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """Try to parse a value as float, return None on failure."""
        if value is None or value == "":
            return None
        try:
            v = float(value)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None
