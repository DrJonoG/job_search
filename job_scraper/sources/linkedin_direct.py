"""
LinkedIn (Direct) – in-house scraper for LinkedIn job search.

Runs alongside the JobSpy-based "LinkedIn" source to catch listings that JobSpy may miss.
Uses LinkedIn's jobs-guest API (seeMoreJobPostings/search), which returns HTML job cards
without requiring JavaScript. The /jobs/search/ webpage is JS-rendered and returns no cards
to a plain HTTP client. No API key; no jobspy dependency.

Time filter f_TPR: past 24h = r86400, past week = r604800, past month = r2592000.
Use LINKEDIN_DIRECT_DELAY to avoid blocks.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlencode, urljoin

import config
from ..models import Job
from .base import BaseSource, normalize_keywords

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
JOBS_SEARCH_PATH = "/jobs/search/"
# Guest API returns HTML job cards without requiring JS; the /jobs/search/ page is JS-rendered and returns no cards.
# See https://gist.github.com/Diegiwg/51c22fa7ec9d92ed9b5d1f537b9e1107
SEARCH_API_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# f_TPR: time posted filter (guest API and website accept these)
# Past 24 hours = r86400, past week = r604800, past month = r2592000, any = ""
F_TPR_24H = "r86400"
F_TPR_WEEK = "r604800"
F_TPR_MONTH = "r2592000"

# Delay between pagination requests (seconds). Be conservative to avoid blocks.
LINKEDIN_DIRECT_DELAY = getattr(config, "LINKEDIN_DIRECT_DELAY", 5.0)
# Browser mode: use Playwright to open the real page (logged-in session possible)
LINKEDIN_DIRECT_USE_BROWSER = getattr(config, "LINKEDIN_DIRECT_USE_BROWSER", False)
LINKEDIN_DIRECT_BROWSER_HEADED = getattr(config, "LINKEDIN_DIRECT_BROWSER_HEADED", False)
LINKEDIN_DIRECT_BROWSER_PROFILE = getattr(config, "LINKEDIN_DIRECT_BROWSER_PROFILE", "")
# Delay between clicking individual job cards in browser mode (seconds)
LINKEDIN_DIRECT_CARD_DELAY = getattr(config, "LINKEDIN_DIRECT_CARD_DELAY", 1.0)


class LinkedInDirectSource(BaseSource):
    name = "LinkedIn (Direct)"
    requires_api_key = False
    base_url = BASE_URL

    def __init__(self) -> None:
        super().__init__()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.session.headers["Accept-Language"] = "en-US,en;q=0.9"

    def is_available(self) -> bool:
        try:
            __import__("bs4")
            return True
        except ImportError:
            return False

    def _f_tpr(self, posted_in_last_days: Optional[int]) -> str:
        """Return f_TPR query value for time filter."""
        if not posted_in_last_days or posted_in_last_days <= 0:
            return ""
        if posted_in_last_days <= 1:
            return F_TPR_24H
        if posted_in_last_days <= 7:
            return F_TPR_WEEK
        return F_TPR_MONTH

    def _scroll_job_list(self, page, item_pause: float = 0.45, max_items: int = 40) -> None:
        """Scroll each job list item into view so LinkedIn fills placeholder content (occlusion/virtualization)."""
        try:
            locator = page.locator(
                "li.jobs-search-results__list-item, li[data-occludable-job-id], li.scaffold-layout__list-item"
            )
            n = locator.count()
            if n == 0:
                return
            logger.info("[%s] Expanding %d list items (scroll-into-view)", self.name, min(n, max_items))
            for i in range(min(n, max_items)):
                try:
                    locator.nth(i).scroll_into_view_if_needed(timeout=3000)
                    time.sleep(item_pause)
                except Exception:
                    pass
        except Exception:
            pass

    def _build_website_search_url(
        self,
        keyword: str,
        location: str,
        remote: str,
        posted_in_last_days: Optional[int],
        start: int,
    ) -> str:
        """Build the full LinkedIn jobs search URL (website) for browser mode."""
        params: List[tuple] = [
            ("keywords", keyword),
            ("location", location or "United States"),
            ("sortBy", "DD"),
        ]
        if remote == "Remote":
            params.append(("f_WT", "2"))
        f_tpr = self._f_tpr(posted_in_last_days)
        if f_tpr:
            params.append(("f_TPR", f_tpr))
        params.append(("start", start))
        return f"{BASE_URL}{JOBS_SEARCH_PATH}?{urlencode(params)}"

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
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("[%s] beautifulsoup4 not installed", self.name)
            return []

        keywords_list = normalize_keywords(keywords, default=["jobs"])
        location_query = location.strip() if location else ""
        if location_query:
            locations_to_search = [location_query]
        else:
            locations_to_search = getattr(config, "LINKEDIN_DIRECT_LOCATIONS", ["United States"]) or ["United States"]
        on_batch = kwargs.get("on_batch")

        if LINKEDIN_DIRECT_USE_BROWSER:
            return self._fetch_jobs_via_browser(
                keywords_list=keywords_list,
                locations_to_search=locations_to_search,
                remote=remote,
                posted_in_last_days=posted_in_last_days,
                max_results=max_results,
                on_batch=on_batch,
            )
        jobs = self._fetch_jobs_guest_api(
            keywords_list=keywords_list,
            locations_to_search=locations_to_search,
            remote=remote,
            posted_in_last_days=posted_in_last_days,
            max_results=max_results,
            on_batch=on_batch,
        )
        logger.info("[%s] Fetched %d jobs", self.name, len(jobs))
        return jobs

    def _fetch_jobs_via_browser(
        self,
        keywords_list: List[str],
        locations_to_search: List[str],
        remote: str,
        posted_in_last_days: Optional[int],
        max_results: int,
        on_batch: Optional[Callable[[List[Job]], None]] = None,
    ) -> List[Job]:
        """Use Playwright: open the real LinkedIn jobs page, wait for cards, scrape, close. Supports login via persistent profile."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "[%s] Browser mode enabled but playwright not installed. Install with: pip install playwright && playwright install chromium. Falling back to guest API.",
                self.name,
            )
            return self._fetch_jobs_guest_api(
                keywords_list=keywords_list,
                locations_to_search=locations_to_search,
                remote=remote,
                posted_in_last_days=posted_in_last_days,
                max_results=max_results,
            )

        from bs4 import BeautifulSoup

        jobs: List[Job] = []
        seen_urls: set = set()
        page_size = 25
        min_cards_to_continue = 20
        # Cap is per keyword: each keyword gets up to max_results (across all locations)
        max_pages_per_combo = min(
            50, max(5, (max_results + page_size - 1) // page_size // max(1, len(locations_to_search)))
        )
        profile_path = Path(LINKEDIN_DIRECT_BROWSER_PROFILE).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_path),
                    headless=not LINKEDIN_DIRECT_BROWSER_HEADED,
                )
            except Exception as e:
                logger.warning(
                    "[%s] Could not launch browser (close any other browser using the profile): %s. Falling back to guest API.",
                    self.name, e,
                )
                return self._fetch_jobs_guest_api(
                    keywords_list=keywords_list,
                    locations_to_search=locations_to_search,
                    remote=remote,
                    posted_in_last_days=posted_in_last_days,
                    max_results=max_results,
                )
            page = context.new_page()
            page.set_default_timeout(20000)

            # First time / headed: give user a moment to log in if needed (profile may be empty)
            if LINKEDIN_DIRECT_BROWSER_HEADED:
                try:
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=15000)
                    logger.info("[%s] Browser open: log in to LinkedIn if needed (waiting 25s), then search will continue.", self.name)
                    time.sleep(25)
                except Exception:
                    pass

            try:
                for kw_index, keyword in enumerate(keywords_list):
                    jobs_before_keyword = len(jobs)
                    if kw_index > 0:
                        time.sleep(LINKEDIN_DIRECT_DELAY)
                    for loc_index, search_location in enumerate(locations_to_search):
                        if len(jobs) - jobs_before_keyword >= max_results:
                            break
                        if loc_index > 0:
                            time.sleep(LINKEDIN_DIRECT_DELAY)
                        start = 0
                        page_num = 0
                        while page_num < max_pages_per_combo:
                            if len(jobs) - jobs_before_keyword >= max_results:
                                break
                            url = self._build_website_search_url(
                                keyword, search_location.strip() or "United States",
                                remote, posted_in_last_days, start,
                            )
                            logger.info("[%s] Browser: GET %s", self.name, url)
                            if page_num > 0:
                                time.sleep(LINKEDIN_DIRECT_DELAY)
                            try:
                                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                                time.sleep(2)  # Give the SPA a moment to hydrate
                                page.wait_for_selector(
                                    "li.jobs-search-results__list-item, li.scaffold-layout__list-item, div.job-search-card, li div.base-card",
                                    timeout=10000,
                                )
                                self._scroll_job_list(page)
                            except Exception as nav_err:
                                if page_num == 0:
                                    logger.warning("[%s] Browser: no job cards or timeout for '%s' @ %s: %s", self.name, keyword, search_location, nav_err)
                                break
                            # Collect new jobs for this page so we can flush to DB immediately
                            page_batch: List[Job] = []

                            # Detect logged-in split-pane view vs guest view
                            logged_in_locator = page.locator(
                                "li.jobs-search-results__list-item, li.scaffold-layout__list-item"
                            )
                            n_logged_in = logged_in_locator.count()

                            if n_logged_in > 0:
                                # LOGGED-IN VIEW: click each card to load full detail panel
                                n_cards = n_logged_in
                                logger.info(
                                    "[%s] Logged-in view: %d cards found, clicking each for details (remote_filter=%s)",
                                    self.name, n_cards, remote,
                                )
                                added = 0
                                for card_idx in range(n_cards):
                                    if len(jobs) - jobs_before_keyword >= max_results:
                                        break
                                    if card_idx > 0:
                                        time.sleep(LINKEDIN_DIRECT_CARD_DELAY)
                                    job = self._click_and_extract_job(
                                        page, logged_in_locator.nth(card_idx),
                                        keyword, remote_filter=remote,
                                    )
                                    if job and job.url and job.url not in seen_urls:
                                        seen_urls.add(job.url)
                                        jobs.append(job)
                                        page_batch.append(job)
                                        added += 1
                            else:
                                # GUEST VIEW: bulk-parse cards (no detail panel available)
                                html = page.content()
                                soup = BeautifulSoup(html, "html.parser")
                                cards_found = self._find_job_cards(soup)
                                n_cards = len(cards_found) if cards_found else 0
                                if n_cards == 0:
                                    break
                                logger.info(
                                    "[%s] Guest view: %d cards, parsing each (remote_filter=%s)",
                                    self.name, n_cards, remote,
                                )
                                added = 0
                                for card_idx, card in enumerate(cards_found):
                                    if len(jobs) - jobs_before_keyword >= max_results:
                                        break
                                    job = self._parse_card(card, keyword, remote_filter=remote, card_index=card_idx)
                                    if job and job.url and job.url not in seen_urls:
                                        seen_urls.add(job.url)
                                        jobs.append(job)
                                        page_batch.append(job)
                                        added += 1

                            logger.info(
                                "[%s] '%s' @ %s | start=%d: %d cards, %d new (total %d)",
                                self.name, keyword, search_location, start, n_cards, added, len(jobs),
                            )

                            # Flush this page's jobs to DB immediately (crash-safe)
                            if page_batch and on_batch:
                                on_batch(page_batch)

                            page_num += 1
                            if n_cards < min_cards_to_continue:
                                break
                            start += page_size
            finally:
                context.close()

        logger.info("[%s] Fetched %d jobs (browser mode)", self.name, len(jobs))
        return jobs

    def _fetch_jobs_guest_api(
        self,
        keywords_list: List[str],
        locations_to_search: List[str],
        remote: str,
        posted_in_last_days: Optional[int],
        max_results: int,
        on_batch: Optional[Callable[[List[Job]], None]] = None,
    ) -> List[Job]:
        """Original guest-API flow (no browser). Used when browser is off or Playwright unavailable."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        jobs: List[Job] = []
        seen_urls: set = set()
        page_size = 25
        min_cards_to_continue = 20
        # Cap is per keyword
        max_pages_per_combo = min(
            50, max(5, (max_results + page_size - 1) // page_size // max(1, len(locations_to_search)))
        )

        for kw_index, keyword in enumerate(keywords_list):
            jobs_before_keyword = len(jobs)
            if kw_index > 0:
                time.sleep(LINKEDIN_DIRECT_DELAY)
            for loc_index, search_location in enumerate(locations_to_search):
                if len(jobs) - jobs_before_keyword >= max_results:
                    break
                if loc_index > 0:
                    time.sleep(LINKEDIN_DIRECT_DELAY)
                params = {"keywords": keyword, "location": search_location.strip() or "United States"}
                if remote == "Remote":
                    params["f_WT"] = "2"
                f_tpr = self._f_tpr(posted_in_last_days)
                if f_tpr:
                    params["f_TPR"] = f_tpr
                start = 0
                page = 0
                while page < max_pages_per_combo:
                    if len(jobs) - jobs_before_keyword >= max_results:
                        break
                    params["start"] = start
                    if page > 0:
                        time.sleep(LINKEDIN_DIRECT_DELAY)
                    try:
                        resp = self.session.get(SEARCH_API_URL, params=params, timeout=self.timeout)
                        resp.raise_for_status()
                    except Exception as exc:
                        logger.warning("[%s] Request failed (start=%s): %s", self.name, start, exc)
                        break
                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = self._find_job_cards(soup)
                    if not cards:
                        if page == 0:
                            logger.warning(
                                "[%s] No job cards found for '%s' @ %s (start=%s); API may have changed.",
                                self.name, keyword, search_location, start,
                            )
                        break
                    logger.debug("[%s] page has %d cards, parsing each (remote_filter=%s)", self.name, len(cards), remote)
                    page_batch: List[Job] = []
                    added = 0
                    for card_idx, card in enumerate(cards):
                        if len(jobs) - jobs_before_keyword >= max_results:
                            break
                        job = self._parse_card(card, keyword, remote_filter=remote, card_index=card_idx)
                        if job and job.url and job.url not in seen_urls:
                            seen_urls.add(job.url)
                            jobs.append(job)
                            page_batch.append(job)
                            added += 1
                    logger.info("[%s] '%s' @ %s | Page %d (start=%d): %d cards, %d new (total %d)", self.name, keyword, search_location, page + 1, start, len(cards), added, len(jobs))

                    # Flush this page's jobs to DB immediately (crash-safe)
                    if page_batch and on_batch:
                        on_batch(page_batch)

                    page += 1
                    if added == 0 and len(cards) > 0:
                        break
                    if len(cards) < min_cards_to_continue:
                        break
                    start += page_size
        return jobs

    def _find_job_cards(self, soup) -> list:
        """
        Find job card elements.
        Prioritizes Logged-In List items (Scaffold & Classic), then Guest Cards.
        """
        # 1. LOGGED IN VIEW: list items (both new Scaffold and classic layouts)
        cards = soup.select("li.jobs-search-results__list-item, li.scaffold-layout__list-item")
        if cards:
            return cards

        # 2. GUEST VIEW: standard grid/list cards
        selectors = [
            "div.job-search-card",
            "div.base-card",
            "li div.base-card",
            "a.base-card__full-link",
        ]
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                return cards

        # 3. Fallback: loose links only if containers failed (filter out non-job links)
        for link_sel in ("a[href*='/jobs/view/']", "a[href*='currentJobId']"):
            job_links = soup.select(link_sel)
            if job_links:
                valid_links = [
                    a for a in job_links
                    if "premium/products" not in a.get("href", "")
                    and "login" not in a.get("href", "")
                ]
                if valid_links:
                    return valid_links
        return []

    # ── Logged-in browser: click card → extract detail panel ─────

    def _click_and_extract_job(
        self,
        page,
        card_locator,
        keyword: str,
        remote_filter: str = "Any",
    ):
        """Click a job card in the logged-in split-pane view to load the detail panel, then extract full Job data."""
        try:
            card_locator.scroll_into_view_if_needed(timeout=3000)
            card_locator.click(timeout=5000)

            # Wait for the description content to appear in the detail panel
            try:
                page.wait_for_selector(
                    "#job-details .mt4, .jobs-description-content__text--stretch",
                    timeout=8000,
                )
            except Exception:
                time.sleep(2)
            time.sleep(0.5)

            # --- TITLE ---
            title = ""
            try:
                title_loc = page.locator(".job-details-jobs-unified-top-card__job-title h1")
                if title_loc.count() > 0:
                    title = (title_loc.first.inner_text() or "").strip()
            except Exception:
                pass
            # De-dupe doubled titles (sr-only issue)
            if title and len(title) >= 2 and len(title) % 2 == 0:
                half = len(title) // 2
                if title[:half] == title[half:]:
                    title = title[:half]
            title = title or keyword

            # --- URL ---
            href = ""
            try:
                link_loc = page.locator(".job-details-jobs-unified-top-card__job-title h1 a")
                if link_loc.count() > 0:
                    href = (link_loc.first.get_attribute("href") or "").strip()
            except Exception:
                pass
            if href and not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            # Normalise to canonical /jobs/view/<id>/ URL
            if href:
                m = re.search(r"/jobs/view/(\d+)", href)
                if m:
                    href = f"{BASE_URL}/jobs/view/{m.group(1)}/"
            if not href:
                return None

            # --- COMPANY ---
            company = ""
            try:
                comp_loc = page.locator(".job-details-jobs-unified-top-card__company-name a")
                if comp_loc.count() > 0:
                    company = (comp_loc.first.inner_text() or "").strip()
            except Exception:
                pass
            company = company or "Unknown"

            # --- LOCATION & DATE POSTED ---
            location = ""
            date_posted = ""
            try:
                tertiary = page.locator(
                    ".job-details-jobs-unified-top-card__tertiary-description-container"
                )
                if tertiary.count() > 0:
                    low_spans = tertiary.locator(".tvm__text--low-emphasis")
                    if low_spans.count() > 0:
                        location = (low_spans.first.text_content() or "").strip()
                    # Use text_content() to avoid CSS truncation ("4 hours ag" etc.)
                    date_span = tertiary.locator(".tvm__text--positive")
                    if date_span.count() > 0:
                        raw_date = (date_span.first.text_content() or "").strip()
                        date_posted = self._resolve_relative_date(raw_date)
            except Exception:
                pass
            if not date_posted:
                date_posted = datetime.now().strftime("%Y-%m-%d")

            # --- DESCRIPTION ---
            description = ""
            try:
                desc_loc = page.locator("#job-details")
                if desc_loc.count() > 0:
                    description = (desc_loc.first.inner_text() or "").strip()
                    # Strip the "About the job" heading
                    if description.lower().startswith("about the job"):
                        description = description[len("about the job"):].strip()
            except Exception:
                pass

            # --- SALARY, REMOTE, JOB TYPE from preference badges ---
            salary_min = None
            salary_max = None
            salary_currency = ""
            job_type = ""
            is_remote = False
            try:
                pref_buttons = page.locator(".job-details-fit-level-preferences button")
                for i in range(pref_buttons.count()):
                    btn_text = (pref_buttons.nth(i).inner_text() or "").strip()
                    # Salary pattern: £70K/yr - £75K/yr  or  $120,000/yr etc.
                    sal_match = re.search(
                        r'([£$€])\s*([\d,.]+[Kk]?)(?:/yr)?\s*(?:-\s*[£$€]?\s*([\d,.]+[Kk]?)(?:/yr)?)?',
                        btn_text,
                    )
                    if sal_match:
                        salary_currency = {"£": "GBP", "$": "USD", "€": "EUR"}.get(
                            sal_match.group(1), ""
                        )
                        salary_min = self._parse_salary_amount(sal_match.group(2))
                        if sal_match.group(3):
                            salary_max = self._parse_salary_amount(sal_match.group(3))
                        continue
                    # Remote badge
                    if re.search(r'\bRemote\b', btn_text, re.IGNORECASE):
                        is_remote = True
                        continue
                    # Job type badge
                    jt_match = re.search(
                        r'(Full-time|Part-time|Contract|Internship|Temporary)',
                        btn_text, re.IGNORECASE,
                    )
                    if jt_match:
                        job_type = jt_match.group(1)
                        continue
            except Exception:
                pass

            # Fallback remote detection from text
            if not is_remote:
                is_remote = bool(
                    re.search(r'(remote|wfh|work from home)', f"{location} {title}", re.IGNORECASE)
                )
            if remote_filter == "Remote" and not is_remote:
                return None

            # --- COMPANY LOGO ---
            logo = ""
            try:
                logo_loc = page.locator(
                    ".job-details-jobs-unified-top-card__container--two-pane .ivm-view-attr__img-wrapper img"
                )
                if logo_loc.count() > 0:
                    logo = (logo_loc.first.get_attribute("src") or "").strip()
            except Exception:
                pass

            logger.debug(
                "[%s] Extracted: '%s' @ %s | desc=%d chars | salary=%s-%s %s | type=%s | remote=%s",
                self.name, title, company, len(description),
                salary_min, salary_max, salary_currency, job_type, is_remote,
            )

            return Job(
                title=title,
                company=company,
                location=location,
                description=description,
                url=href,
                source=self.name,
                remote="Remote" if is_remote else "On-site",
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=salary_currency,
                job_type=job_type,
                date_posted=date_posted,
                company_logo=logo,
            )
        except Exception as e:
            logger.warning("[%s] Error clicking/extracting job detail: %s", self.name, e)
            return None

    @staticmethod
    def _parse_salary_amount(text: str) -> Optional[float]:
        """Parse salary text like '70K', '75,000', '70K/yr' into a float."""
        if not text:
            return None
        text = text.replace(",", "").replace("/yr", "").strip()
        multiplier = 1000 if text.upper().endswith("K") else 1
        text = text.rstrip("Kk")
        try:
            return float(text) * multiplier
        except ValueError:
            return None

    @staticmethod
    def _resolve_relative_date(text: str) -> str:
        """Convert relative date text ('3 hours ago', 'Reposted 2 days ago') to YYYY-MM-DD.

        Returns today's date for anything that doesn't look like a recognisable
        relative-time string (guards against garbled text like 'Company re').
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if not text:
            return today

        clean = text.strip().lower()
        # Strip common prefixes LinkedIn prepends
        clean = re.sub(r'^(reposted|posted)\s+', '', clean)

        # Already an ISO date
        if re.match(r'^\d{4}-\d{2}-\d{2}', clean):
            return clean[:10]

        # Must contain a time-related keyword to be a valid relative date
        if not re.search(r'(just now|moment|today|second|minute|hour|day|week|month|year|ago)', clean):
            return today

        # "just now", "moments ago", "today"
        if re.search(r'(just now|moment|today)', clean):
            return today

        # Seconds / minutes / hours → today
        if re.search(r'\d+\s*(second|minute|hour)', clean):
            return today

        # Days
        m = re.search(r'(\d+)\s*day', clean)
        if m:
            return (datetime.now() - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")

        # Weeks
        m = re.search(r'(\d+)\s*week', clean)
        if m:
            return (datetime.now() - timedelta(weeks=int(m.group(1)))).strftime("%Y-%m-%d")

        # Months (approximate)
        m = re.search(r'(\d+)\s*month', clean)
        if m:
            return (datetime.now() - timedelta(days=int(m.group(1)) * 30)).strftime("%Y-%m-%d")

        # Years (approximate)
        m = re.search(r'(\d+)\s*year', clean)
        if m:
            return (datetime.now() - timedelta(days=int(m.group(1)) * 365)).strftime("%Y-%m-%d")

        return today

    # ── Card parsing (BS4 – used for guest API & guest browser fallback) ──

    def _parse_card(self, card, fallback_title: str, remote_filter: str = "Any", card_index: int = -1):
        """Extract Job from a job card. Handles Logged-In (li.jobs-search-results__list-item) and Guest DOM."""
        try:
            # --- 1. TITLE ---
            title_el = card.select_one(
                ".job-card-list__title, .artdeco-entity-lockup__title, .base-search-card__title, "
                "h3.base-search-card__title, a.job-card-container__link strong"
            )
            if title_el:
                # Remove screen-reader-only / hidden spans that duplicate visible text
                for hidden in title_el.select('.sr-only, .visually-hidden, [aria-hidden="true"]'):
                    hidden.decompose()
                title = title_el.get_text(strip=True).strip()
            else:
                title = ""
            # Safety net: detect exact doubled titles (e.g. "TitleTitle" → "Title")
            if title and len(title) >= 2 and len(title) % 2 == 0:
                half = len(title) // 2
                if title[:half] == title[half:]:
                    title = title[:half]
            title = title or fallback_title

            # --- 2. LINK ---
            link_el = card.select_one(
                "a.job-card-container__link, a.base-card__full-link, a[href*='/jobs/view/'], a[href*='currentJobId']"
            )
            href = ""
            if link_el:
                href = link_el.get("href", "").strip()
            elif card.name == "a":
                href = card.get("href", "").strip()
            if href and not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            # Filter: reject Premium/Search links
            if not href or "/jobs/" not in href or "premium/products" in href:
                return None

            # --- 3. COMPANY ---
            company_el = card.select_one(
                ".job-card-container__primary-description, .artdeco-entity-lockup__subtitle, "
                ".base-search-card__subtitle, h4.base-search-card__subtitle"
            )
            company = (company_el.get_text(strip=True) if company_el else "").strip() or "Unknown"

            # --- 4. LOCATION ---
            location_el = card.select_one(
                ".job-card-container__metadata-item, .artdeco-entity-lockup__caption, .job-search-card__location"
            )
            loc = (location_el.get_text(strip=True) if location_el else "").strip()

            # --- 5. REMOTE ---
            is_remote = bool(
                re.search(r"(remote|wfh|work from home)", loc, re.IGNORECASE)
                or re.search(r"(remote|wfh|work from home)", title, re.IGNORECASE)
            )
            if remote_filter == "Remote" and not is_remote:
                return None

            # --- 6. DATE ---
            date_posted = ""
            time_el = card.select_one("time")
            if time_el:
                # Prefer the datetime attribute (ISO date) if available
                dt_attr = (time_el.get("datetime") or "").strip()
                if dt_attr and re.match(r"^\d{4}-\d{2}-\d{2}", dt_attr):
                    date_posted = dt_attr[:10]
                else:
                    date_posted = self._resolve_relative_date(time_el.get_text(strip=True))
            if not date_posted:
                date_posted = datetime.now().strftime("%Y-%m-%d")

            return Job(
                title=title,
                company=company,
                location=loc,
                description="",
                url=href,
                source=self.name,
                remote="Remote" if is_remote else "On-site",
                date_posted=date_posted,
            )
        except Exception as e:
            logger.warning("[%s] Error parsing card %s: %s", self.name, card_index, e)
            return None
