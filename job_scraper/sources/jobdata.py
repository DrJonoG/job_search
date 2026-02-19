"""
JobData API – job listings with advanced filters.
Docs: https://jobdataapi.com/docs/  |  Jobs: https://jobdataapi.com/c/jobs-api-endpoint-documentation/

Without an API key: ~10 requests/hour (testing). With a key: no hourly limit; keep requests
sequential and cache results. Set JOBDATA_API_KEY in .env for production use.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import List, Optional

import config
from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

JOBDATA_BASE_URL = "https://jobdataapi.com/api/jobs/"
# Without API key: 10 requests per hour (anonymous). We persist timestamps so limits apply across runs.
JOBDATA_ANON_MAX_PER_HOUR = 10
RATELIMIT_FILE = config.LOG_DIR / "jobdata_ratelimit.json"


def _anon_request_budget() -> bool:
    """Return True if we have budget for one more anonymous request this hour; else False. Side effect: consumes one."""
    path = Path(RATELIMIT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    window = 3600  # 1 hour

    try:
        data = json.loads(path.read_text()) if path.exists() else {"timestamps": []}
    except Exception:
        data = {"timestamps": []}

    timestamps = [t for t in data["timestamps"] if now - t < window]
    if len(timestamps) >= JOBDATA_ANON_MAX_PER_HOUR:
        logger.warning(
            "[JobData] Anonymous limit reached (%d requests in the last hour). Set JOBDATA_API_KEY for more.",
            len(timestamps),
        )
        return False

    timestamps.append(now)
    data["timestamps"] = timestamps
    try:
        path.write_text(json.dumps(data))
    except Exception as e:
        logger.warning("[JobData] Could not write rate-limit file: %s", e)
    return True


class JobDataSource(BaseSource):
    name = "JobData"
    requires_api_key = False  # optional; works without key with strict limit
    base_url = JOBDATA_BASE_URL

    def is_available(self) -> bool:
        return True  # Works without key (rate-limited)

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
        api_key = getattr(config, "JOBDATA_API_KEY", "") or ""
        keywords_list = normalize_keywords(keywords, default=["developer"])
        # Without key: one request per keyword, up to 10/hour (checked inside loop).

        base_params: dict = {
            "description_str": "true",
        }
        if location and len(location.strip()) >= 3:
            base_params["location"] = location.strip()
        if remote == "Remote":
            base_params["has_remote"] = "true"
        if salary_min is not None:
            base_params["min_salary"] = int(salary_min)
        if posted_in_last_days and posted_in_last_days > 0:
            base_params["max_age"] = min(posted_in_last_days, 999)
        if experience_level:
            level_map = {
                "entry": "EN",
                "mid": "MI",
                "senior": "SE",
                "lead": "SE",
                "executive": "EX",
            }
            code = level_map.get(experience_level.strip().lower())
            if code:
                base_params["experience_level"] = code
        countries = getattr(config, "JOBDATA_COUNTRIES", None) or []
        if countries:
            base_params["country_code"] = countries
        if api_key:
            base_params["page_size"] = min(5000, max(1, max_results))

        headers = {}
        if api_key:
            headers["Authorization"] = f"Api-Key {api_key}"

        max_pages_per_keyword = 20  # per keyword

        jobs: List[Job] = []
        seen_urls: set = set()
        try:
            for keyword in keywords_list:
                jobs_before_keyword = len(jobs)
                if not api_key and not _anon_request_budget():
                    break
                params = dict(base_params)
                params["title"] = keyword if len(keyword) >= 3 else "developer"
                page = 1
                pages_this_keyword = 0
                while len(jobs) - jobs_before_keyword < max_results and pages_this_keyword < max_pages_per_keyword:
                    if api_key:
                        params["page"] = page
                    time.sleep(self.rate_limit_delay)
                    resp = self.session.get(
                        self.base_url,
                        params=params,
                        headers=headers or None,
                        timeout=self.timeout,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    results = data.get("results") or []
                    if not results:
                        break
                    for item in results:
                        if len(jobs) - jobs_before_keyword >= max_results:
                            break
                        job = self._item_to_job(item)
                        if job and job.url and job.url not in seen_urls:
                            seen_urls.add(job.url)
                            jobs.append(job)
                    if not api_key:
                        break
                    if not data.get("next"):
                        break
                    page += 1
                    pages_this_keyword += 1
        except Exception as exc:
            logger.exception("[JobData] Request failed: %s", exc)
            return jobs

        # API doesn't expose sort; return most recent first by date_posted (newest first, empty last)
        def _sort_key(j: Job) -> tuple:
            d = (j.date_posted or "").strip()
            if d and len(d) >= 10 and d[:10].replace("-", "").isdigit():
                return (0, d)  # valid ISO date, sort desc by string (newest first)
            return (1, "")     # no date last

        jobs.sort(key=_sort_key, reverse=True)

        logger.info("[JobData] Fetched %d jobs (newest first)", len(jobs))
        return jobs

    def _item_to_job(self, item: dict) -> Optional[Job]:
        title = (item.get("title") or "").strip()
        if not title:
            return None
        company_obj = item.get("company") or {}
        company = (company_obj.get("name") or "").strip() if isinstance(company_obj, dict) else ""
        loc = (item.get("location") or "").strip()
        url = (item.get("application_url") or "").strip()
        if not url:
            url = f"https://jobdataapi.com/api/jobs/{item.get('id', '')}/"
        desc = (item.get("description_string") or item.get("description") or "").strip()
        if isinstance(desc, str) and len(desc) > 5000:
            desc = desc[:5000]
        published = item.get("published") or ""
        if isinstance(published, str) and len(published) >= 10:
            date_posted = published[:10]
        else:
            date_posted = ""
        has_remote = item.get("has_remote") is True
        remote = "Remote" if has_remote else "On-site"
        salary_min = self._safe_float(item.get("salary_min"))
        salary_max = self._safe_float(item.get("salary_max"))
        salary_currency = (item.get("salary_currency") or "").strip() or "USD"
        exp_level = (item.get("experience_level") or "").strip()
        exp_map = {"EN": "Entry", "MI": "Mid", "SE": "Senior", "EX": "Executive"}
        experience_level = exp_map.get(exp_level.upper(), exp_level)
        logo = ""
        if isinstance(company_obj, dict) and company_obj.get("logo"):
            logo = (company_obj.get("logo") or "").strip()
        return Job(
            title=title,
            company=company or "Unknown",
            location=loc,
            description=desc,
            url=url,
            source=self.name,
            remote=remote,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            job_type="",
            experience_level=experience_level,
            date_posted=date_posted,
            company_logo=logo,
        )

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
