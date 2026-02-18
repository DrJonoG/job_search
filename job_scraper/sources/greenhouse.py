"""
Greenhouse ATS – public job board API (no key).
Fetches jobs from a list of company board tokens (e.g. stripe, gitlab, github).
API: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

# Default set of well-known Greenhouse board tokens (from boards.greenhouse.io/<token>)
# Boards that 404 (moved/renamed) have been removed to avoid log noise. Add more via GREENHOUSE_BOARD_TOKENS in .env
DEFAULT_BOARDS = [
    # Payments & fintech
    "stripe", "brex", "robinhood", "chime", "affirm", "marqeta",
    "checkr", "mercury", "remotecom",
    # Dev tools & infra
    "gitlab", "jetbrains",
    "datadog", "newrelic", "honeycomb", "pagerduty", "launchdarkly",
    "vercel", "netlify", "cloudflare",
    "mongodb", "elastic", "cockroachlabs", "planetscale",
    "twilio", "mixpanel", "amplitude", "braze", "customerio",
    # Product & design
    "figma", "asana", "airtable", "webflow",
    # AI & data
    "anthropic", "databricks", "fivetran",
    # E‑commerce & marketplace
    "instacart", "gopuff", "getir", "gorillas",
    "flexport", "shipbob", "fabric", "bolt",
    # Social & comms
    "discord", "reddit", "pinterest", "snap", "spotify", "soundcloud",
    "twitch", "kick",
    # Other tech
    "automattic", "canonical", "dropbox", "box", "zapier", "hubspot",
    "salesforce", "servicenow", "workday", "okta", "crowdstrike", "paloaltonetworks",
    "lattice", "rippling", "gusto", "justworks", "remote",
    "superhuman", "loom", "calendly", "cal", "front", "intercom", "zendesk",
    "contentful", "sanity", "builderio", "webflow",
    "1password", "bitwarden", "dashlane",
    "nvidia", "amd", "qualcomm", "intel",
    "rivian", "lucid", "nuro", "waymo", "cruise", "aurora", "zoox",
    "spacex", "relativityspace", "blueorigin", "planet",
    "oscar", "devoted", "clover", "brighthealth", "alignment",
    "coursera", "udemy", "duolingo", "quizlet", "chegg", "coursehero",
    "niantic", "roblox", "unity", "epicgames", "scopely",
    "vimeo", "dailymotion", "vimeo",
    "yelp", "tripadvisor", "expedia", "booking",
    "bloomberg", "reuters", "theguardian",
    "nytimes", "washingtonpost", "voxmedia", "vice", "buzzfeed",
    "warbyparker", "allbirds", "glossier", "casper", "away",
    "nordstrom", "target", "walmart", "bestbuy", "homedepot",
    "imgur", "stackoverflow", "quora",
    "circleci", "travisci",
    "samsara", "convoy", "project44",
    "carta", "capshare", "pulley", "ledgy",
    "lendable", "prosper", "sofi", "better", "blend",
    "cerebras", "sambanova", "graphcore",
    "scale", "labelbox", "scaleai", "outlier",
    "anduril", "palantir", "shieldai",
    "zenefits", "namely", "bamboohr",
    "figment", "chainalysis", "circle", "coinbase", "kraken", "gemini",
    "stability", "midjourney", "runway",
    "coda", "clickup",
    "fastly", "akamai",
    "vonage", "bandwidth", "messagebird",
    "mparticle", "heap",
    "intercom", "zendesk", "freshdesk", "helpscout", "crisp",
    "snyk", "sonarqube", "veracode", "checkmarx",
    "drata", "secureframe", "vanta", "thoropass",
]


class GreenhouseSource(BaseSource):
    name = "Greenhouse"
    requires_api_key = False
    base_url = "https://boards-api.greenhouse.io/v1/boards"

    def __init__(self) -> None:
        super().__init__()
        self._boards = self._get_board_list()

    def _get_board_list(self) -> List[str]:
        try:
            import config
            tokens = getattr(config, "GREENHOUSE_BOARD_TOKENS", None)
            if tokens and isinstance(tokens, str):
                return [t.strip() for t in tokens.split(",") if t.strip()]
            if tokens and isinstance(tokens, list):
                return list(tokens)
        except Exception:
            pass
        return DEFAULT_BOARDS

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
        for board in self._boards:
            if len(jobs) >= max_results:
                break
            try:
                url = f"{self.base_url}/{board}/jobs"
                resp = self._get(url)
                data = resp.json()
            except Exception as exc:
                logger.debug("[%s] Skip board %s: %s", self.name, board, exc)
                continue

            listing = data.get("jobs", []) if isinstance(data, dict) else []
            for item in listing:
                if len(jobs) >= max_results:
                    break

                title = item.get("title", "")
                company = item.get("company_name", board.title())
                job_url = item.get("absolute_url", "")
                loc = item.get("location", {})
                loc_name = loc.get("name", "") if isinstance(loc, dict) else str(loc)
                first_pub = item.get("first_published", "")

                searchable = f"{title} {company} {loc_name}"
                if not self._matches_keywords(searchable, keywords):
                    continue

                is_remote = "remote" in loc_name.lower()
                if remote == "On-site" and is_remote:
                    continue
                if remote == "Remote" and not is_remote:
                    continue

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=loc_name,
                    description="",
                    url=job_url,
                    source=self.name,
                    remote="Remote" if is_remote else "On-site",
                    date_posted=first_pub[:10] if first_pub else "",
                    tags=board,
                ))

        logger.info("[%s] Found %d jobs", self.name, len(jobs))
        return jobs
