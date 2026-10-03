#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
import re
import sys
from pathlib import Path

# Make project package importable when running tools/resume_matcher.py directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config

import numpy as np
from sentence_transformers import SentenceTransformer

from job_scraper.storage import _get_conn


MODEL_NAME = "BAAI/bge-small-en-v1.5"


# ============================================================
# Career targeting
# ============================================================

# Approximate Grand Rapids <= ~20 mile area using city names.
# Remote jobs are also allowed.
LOCAL_LOCATION_TERMS = (
    "grand rapids",
    "kentwood",
    "wyoming, mi",
    "wyoming, michigan",
    "grandville",
    "walker, mi",
    "walker, michigan",
    "east grand rapids",
    "ada, mi",
    "ada, michigan",
    "cascade",
    "comstock park",
    "jenison",
    "rockford, mi",
    "rockford, michigan",
    "caledonia, mi",
    "caledonia, michigan",
)


# Titles that are clearly the wrong career family.
HARD_EXCLUDE_TITLE_PATTERNS = [
    r"\baccount executive\b",
    r"\bsales engineer\b",
    r"\bsales representative\b",
    r"\bsales development\b",
    r"\bbusiness development representative\b",
    r"\bdata engineer\b",
    r"\bdata scientist\b",
    r"\bdata analyst\b",
    r"\bsoftware engineer\b",
    r"\bsoftware developer\b",
    r"\bweb developer\b",
    r"\bdevops engineer\b",
    r"\bcloud engineer\b",
    r"\bnetwork engineer\b",
    r"\bsystems engineer\b",
    r"\bsecurity engineer\b",
    r"\bsolutions architect\b",
    r"\bsolution architect\b",
    r"\bproduct manager\b",
    r"\bproject manager\b",
    r"\bscrum master\b",
    r"\baudit officer\b",
    r"\bchief audit officer\b",
    r"\baudit\b",
    r"\bsales\b",
    r"\brevenue cycle\b",
    r"\brevenue technology\b",
    r"\bdirector of engineering\b",
]


# Technology areas that make Director/VP/Head roles relevant.
TECH_SCOPE_PATTERN = re.compile(
    r"\b("
    r"information technology|"
    r"technology|"
    r"\bit\b|"
    r"infrastructure|"
    r"cloud|"
    r"cybersecurity|"
    r"cyber security|"
    r"security technology|"
    r"enterprise systems|"
    r"business technology|"
    r"technology operations|"
    r"it operations|"
    r"digital technology|"
    r"enterprise technology"
    r")\b",
    re.IGNORECASE,
)


def evaluate_title(title: str) -> tuple[bool, float, float, str]:
    """
    Returns:
        eligible,
        role/title score 0..1,
        seniority score 0..1,
        reason
    """

    t = (title or "").strip().lower()

    if not t:
        return False, 0.0, 0.0, "missing title"

    # Exact executive technology titles always win before generic exclusions.
    if re.search(r"\bchief technology officer\b|\bfield chief technology officer\b", t):
        return True, 1.00, 1.00, "CTO"

    if re.search(r"\bchief information officer\b", t):
        return True, 1.00, 1.00, "CIO"

    # Acronyms only when used as real title words.
    if re.search(r"(^|[\s(/,-])cto($|[\s)/,-])", t):
        return True, 1.00, 1.00, "CTO"

    if re.search(r"(^|[\s(/,-])cio($|[\s)/,-])", t):
        return True, 1.00, 1.00, "CIO"

    # Remove obvious unrelated career families.
    for pattern in HARD_EXCLUDE_TITLE_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return False, 0.0, 0.0, f"excluded title: {pattern}"

    has_tech_scope = bool(TECH_SCOPE_PATTERN.search(t))

    # VP / Vice President
    if re.search(r"\bvice president\b|\bvp\b", t):
        if has_tech_scope:
            return True, 0.96, 0.96, "VP technology leadership"
        return False, 0.0, 0.0, "VP outside technology"

    # Head of...
    if re.search(r"\bhead of\b", t):
        if has_tech_scope:
            return True, 0.92, 0.92, "Head technology leadership"
        return False, 0.0, 0.0, "Head outside technology"

    # Senior Director
    if re.search(r"\bsenior director\b", t):
        if has_tech_scope:
            return True, 0.92, 0.90, "Senior Director technology"
        return False, 0.0, 0.0, "Senior Director outside technology"

    # Director
    if re.search(r"\bdirector\b", t):
        if has_tech_scope:
            return True, 0.88, 0.85, "Director technology"
        return False, 0.0, 0.0, "Director outside technology"

    # Senior Manager and below are outside the target level.
    return False, 0.0, 0.0, "below/outside target leadership"


def evaluate_location(location: str) -> tuple[bool, str]:
    loc = (location or "").strip().lower()

    if not loc:
        return False, "missing location"

    # Anything explicitly represented as remote by the scraper
    if re.search(r"\bremote\b", loc):
        return True, "remote"

    # Grand Rapids-area cities
    for term in LOCAL_LOCATION_TERMS:
        if term in loc:
            return True, "Grand Rapids area"

    return False, "outside location target"


# ============================================================
# Resume loading
# ============================================================

def load_text(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if suffix == ".docx":
        from docx import Document

        doc = Document(str(path))
        return "\n".join(
            p.text for p in doc.paragraphs if p.text.strip()
        )

    raise ValueError(
        f"Unsupported resume format: {suffix}. "
        "Use .txt, .md, .pdf, or .docx"
    )


def clean_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def chunk_resume(text: str, max_chars: int = 1400) -> list[str]:
    blocks = []

    for block in re.split(r"\n\s*\n", text):
        block = block.strip()

        if not block:
            continue

        if len(block) > max_chars:
            current = []

            for line in block.splitlines():
                line = line.strip()

                if not line:
                    continue

                candidate = "\n".join(current + [line])

                if current and len(candidate) > max_chars:
                    blocks.append("\n".join(current))
                    current = [line]
                else:
                    current.append(line)

            if current:
                blocks.append("\n".join(current))

        else:
            blocks.append(block)

    blocks = [b for b in blocks if len(b) >= 40]

    if not blocks:
        blocks = [text[:max_chars]]

    return blocks


# ============================================================
# Database
# ============================================================

def ensure_match_table():
    conn = _get_conn()

    try:
        cur = conn.cursor(buffered=True)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_match_scores (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                resume_hash VARCHAR(64) NOT NULL,
                job_id VARCHAR(255) NOT NULL,

                match_score DECIMAL(6,2) NOT NULL,
                semantic_score DECIMAL(6,2) NOT NULL,
                title_score DECIMAL(6,2) NOT NULL,

                best_resume_chunk TEXT NULL,
                model_name VARCHAR(255) NOT NULL,

                analyzed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

                UNIQUE KEY uq_resume_job (resume_hash, job_id),
                INDEX idx_job_id (job_id),
                INDEX idx_match_score (match_score)
            )
            """
        )

        # Schema migration: associate scores with a Job Search AI Prompt.
        cur.execute(
            "SHOW COLUMNS FROM resume_match_scores LIKE 'prompt_id'"
        )
        if cur.fetchone() is None:
            cur.execute(
                """
                ALTER TABLE resume_match_scores
                ADD COLUMN prompt_id BIGINT NOT NULL DEFAULT 0 AFTER id
                """
            )

        # Previous versions uniquely keyed only by resume hash/job.
        cur.execute(
            "SHOW INDEX FROM resume_match_scores WHERE Key_name = 'uq_resume_job'"
        )
        if cur.fetchone() is not None:
            cur.execute(
                "ALTER TABLE resume_match_scores DROP INDEX uq_resume_job"
            )

        cur.execute(
            "SHOW INDEX FROM resume_match_scores "
            "WHERE Key_name = 'uq_prompt_resume_job'"
        )
        if cur.fetchone() is None:
            cur.execute(
                """
                ALTER TABLE resume_match_scores
                ADD UNIQUE KEY uq_prompt_resume_job
                    (prompt_id, resume_hash, job_id)
                """
            )

        conn.commit()
        cur.close()

    finally:
        conn.close()



# ============================================================
# Optional Resume Matcher synchronization
# ============================================================

def _resume_matcher_get_json(
    path: str,
    params: dict | None = None,
) -> dict:
    """GET JSON from the configured Resume Matcher instance."""
    base_url = getattr(
        config,
        "RESUME_MATCHER_URL",
        "",
    ).rstrip("/")

    if not base_url:
        raise RuntimeError(
            "RESUME_MATCHER_URL is not configured"
        )

    url = base_url + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "job-search-resume-matcher/1.0",
        },
    )

    with urllib.request.urlopen(
        req,
        timeout=15,
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def _resume_matcher_resume_to_text(data: dict) -> str:
    """
    Convert Resume Matcher's processed_resume structure into
    the same clean text representation used by Job Search.
    """
    resume = data.get("processed_resume") or {}

    if not isinstance(resume, dict):
        resume = {}

    lines = []

    def add(value=""):
        if value is None:
            return

        value = str(value).strip()

        if value:
            lines.append(value)

    def heading(value):
        if lines and lines[-1] != "":
            lines.append("")

        lines.append(str(value).upper())

    # Personal information
    personal = resume.get("personalInfo") or {}

    add(personal.get("name"))
    add(personal.get("title"))

    contact = [
        personal.get("location"),
        personal.get("email"),
        personal.get("phone"),
    ]

    contact = [
        str(v).strip()
        for v in contact
        if v
    ]

    if contact:
        add(" | ".join(contact))

    add(personal.get("linkedin"))
    add(personal.get("github"))
    add(personal.get("website"))

    # Summary
    summary = resume.get("summary")

    if summary:
        heading("Summary")
        add(summary)

    # Work experience
    experience = resume.get("workExperience") or []

    if experience:
        heading("Experience")

        for job in experience:
            if not isinstance(job, dict):
                continue

            title = (
                job.get("title") or ""
            ).strip()

            company = (
                job.get("company") or ""
            ).strip()

            if title and company:
                add(f"{title} | {company}")
            else:
                add(title or company)

            details = [
                job.get("location"),
                job.get("years"),
            ]

            details = [
                str(v).strip()
                for v in details
                if v
            ]

            if details:
                add(" | ".join(details))

            for bullet in job.get("description") or []:
                bullet = str(bullet).strip()

                if bullet:
                    add(f"- {bullet}")

            lines.append("")

    # Education
    education = resume.get("education") or []

    if education:
        heading("Education")

        for item in education:
            if not isinstance(item, dict):
                continue

            institution = (
                item.get("institution") or ""
            ).strip()

            degree = (
                item.get("degree") or ""
            ).strip()

            if degree and institution:
                add(f"{degree} | {institution}")
            else:
                add(degree or institution)

            add(item.get("years"))

            description = item.get("description")

            if isinstance(description, list):
                for bullet in description:
                    add(f"- {bullet}")

            elif description:
                add(description)

    # Projects
    projects = resume.get("personalProjects") or []

    if projects:
        heading("Projects")

        for project in projects:
            if not isinstance(project, dict):
                continue

            add(
                project.get("title")
                or project.get("name")
            )

            description = (
                project.get("description") or []
            )

            if isinstance(description, list):
                for bullet in description:
                    add(f"- {bullet}")

            elif description:
                add(description)

    # Skills / certifications / languages / awards
    additional = resume.get("additional") or {}

    sections = [
        (
            "Technical Skills",
            additional.get("technicalSkills"),
        ),
        (
            "Certifications & Training",
            additional.get("certificationsTraining"),
        ),
        (
            "Languages",
            additional.get("languages"),
        ),
        (
            "Awards",
            additional.get("awards"),
        ),
    ]

    for label, values in sections:
        if not values:
            continue

        heading(label)

        if isinstance(values, list):
            for value in values:
                add(f"- {value}")

        else:
            add(values)

    # Custom sections
    custom_sections = (
        resume.get("customSections") or {}
    )

    for section_name, section in custom_sections.items():
        if not isinstance(section, dict):
            continue

        heading(
            section_name
            .replace("_", " ")
            .title()
        )

        for item in section.get("items") or []:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            subtitle = item.get("subtitle")

            if title and subtitle:
                add(f"{title} | {subtitle}")
            else:
                add(title or subtitle)

            add(item.get("location"))
            add(item.get("years"))

            description = (
                item.get("description") or []
            )

            if isinstance(description, list):
                for bullet in description:
                    add(f"- {bullet}")

            elif description:
                add(description)

        for value in section.get("strings") or []:
            add(f"- {value}")

        add(section.get("text"))

    # Collapse repeated blank lines
    cleaned = []
    previous_blank = False

    for line in lines:
        blank = not str(line).strip()

        if blank and previous_blank:
            continue

        cleaned.append(line)
        previous_blank = blank

    return "\n".join(cleaned).strip()


def _get_ai_prompt_resume_link(
    prompt_id: int,
) -> dict | None:
    """Return optional external resume link."""
    conn = _get_conn()

    try:
        cur = conn.cursor()

        # Keep Resume Matcher completely optional.
        cur.execute(
            "SHOW TABLES LIKE 'ai_prompt_resume_links'"
        )

        if not cur.fetchone():
            cur.close()
            return None

        cur.close()

        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT
                source,
                external_id,
                external_name,
                synced_at
            FROM ai_prompt_resume_links
            WHERE prompt_id = %s
            LIMIT 1
            """,
            (prompt_id,),
        )

        row = cur.fetchone()
        cur.close()

        return row

    finally:
        conn.close()


def _save_synced_resume(
    prompt_id: int,
    cv_text: str,
) -> None:
    """Update cached Job Search CV and sync timestamp."""
    conn = _get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE ai_prompts
            SET cv = %s
            WHERE id = %s
            """,
            (
                cv_text,
                prompt_id,
            ),
        )

        cur.execute(
            """
            UPDATE ai_prompt_resume_links
            SET synced_at = CURRENT_TIMESTAMP
            WHERE prompt_id = %s
            """,
            (prompt_id,),
        )

        conn.commit()
        cur.close()

    finally:
        conn.close()


def sync_linked_resume_if_changed(
    profile: dict,
) -> tuple[dict, str | None]:
    """
    Check Resume Matcher only for linked profiles.

    Returns the possibly-updated profile plus a human-readable
    status message.

    Resume Matcher failures are intentionally non-fatal:
    Job Search continues with the locally cached CV.
    """
    prompt_id = int(profile.get("id") or 0)

    if not prompt_id:
        return profile, None

    try:
        link = _get_ai_prompt_resume_link(
            prompt_id
        )
    except Exception as exc:
        return (
            profile,
            "Resume Matcher sync: "
            f"link check failed ({exc}); using cached CV",
        )

    if not link:
        # Normal/manual profile.
        return profile, None

    if link.get("source") != "resume_matcher":
        # Future external resume providers remain unaffected.
        return profile, None

    resume_id = (
        link.get("external_id") or ""
    ).strip()

    if not resume_id:
        return (
            profile,
            "Resume Matcher sync: "
            "linked resume ID is missing; using cached CV",
        )

    try:
        payload = _resume_matcher_get_json(
            "/api/v1/resumes",
            {
                "resume_id": resume_id,
            },
        )

        data = payload.get("data") or {}

        latest_text = (
            _resume_matcher_resume_to_text(data)
        )

        # Fallback for older/different Resume Matcher responses.
        if len(latest_text.strip()) < 100:
            raw_resume = data.get(
                "raw_resume"
            ) or {}

            raw_content = (
                raw_resume.get("content") or ""
            )

            try:
                parsed = json.loads(raw_content)

                if isinstance(parsed, dict):
                    latest_text = (
                        _resume_matcher_resume_to_text({
                            "processed_resume": parsed
                        })
                    )
                else:
                    latest_text = raw_content

            except Exception:
                latest_text = raw_content

        latest_text = clean_text(latest_text)

        if len(latest_text) < 200:
            return (
                profile,
                "Resume Matcher sync: "
                "remote resume was empty/too short; using cached CV",
            )

        current_text = clean_text(
            profile.get("cv") or ""
        )

        current_hash = hashlib.sha256(
            current_text.encode("utf-8")
        ).hexdigest()

        latest_hash = hashlib.sha256(
            latest_text.encode("utf-8")
        ).hexdigest()

        if current_hash == latest_hash:
            return (
                profile,
                "Resume Matcher sync: no changes",
            )

        _save_synced_resume(
            prompt_id,
            latest_text,
        )

        # Use the new content immediately in this matcher run.
        profile["cv"] = latest_text

        name = (
            link.get("external_name")
            or resume_id
        )

        return (
            profile,
            "Resume Matcher sync: "
            f"updated linked resume ({name})",
        )

    except Exception as exc:
        return (
            profile,
            "Resume Matcher sync: "
            f"unavailable ({exc}); using cached CV",
        )


def load_ai_profile(prompt_id: int | None = None) -> dict:
    """
    Load Job Search's active AI prompt/profile.

    Expected native fields include:
      cv
      about_me
      preferences
      extra_context
      is_active

    Column discovery is used so this remains tolerant of minor
    schema differences between project versions.
    """
    conn = _get_conn()

    try:
        cur = conn.cursor()

        cur.execute("SHOW COLUMNS FROM ai_prompts")
        columns = {row[0] for row in cur.fetchall()}

        if not columns:
            raise RuntimeError("ai_prompts table exists but has no columns.")

        if "cv" not in columns:
            raise RuntimeError(
                "Could not find native CV field 'cv' in ai_prompts."
            )

        wanted = [
            "id",
            "title",
            "cv",
            "about_me",
            "preferences",
            "extra_context",
            "is_active",
            "created_at",
            "updated_at",
        ]

        select_cols = [c for c in wanted if c in columns]

        if prompt_id is not None:
            if "id" not in columns:
                raise RuntimeError("ai_prompts does not contain an id column.")
            where = "WHERE id = %s"
            query_params = (prompt_id,)
        else:
            where = "WHERE is_active = 1" if "is_active" in columns else ""
            query_params = ()

        order_cols = []

        if "updated_at" in columns:
            order_cols.append("updated_at DESC")

        if "created_at" in columns:
            order_cols.append("created_at DESC")

        if "id" in columns:
            order_cols.append("id DESC")

        order = (
            "ORDER BY " + ", ".join(order_cols)
            if order_cols
            else ""
        )

        sql = f"""
            SELECT {", ".join(select_cols)}
            FROM ai_prompts
            {where}
            {order}
            LIMIT 1
        """

        cur.close()

        cur = conn.cursor(dictionary=True)
        cur.execute(sql, query_params)
        row = cur.fetchone()
        cur.close()

        if not row:
            if "is_active" in columns:
                raise RuntimeError(
                    "No active AI Prompt was found in Job Search. "
                    "Set an AI Prompt/profile active first."
                )

            raise RuntimeError(
                "No AI Prompt/profile was found in Job Search."
            )

        cv = (row.get("cv") or "").strip()

        if len(cv) < 200:
            raise RuntimeError(
                "The active AI Prompt CV is empty or too short."
            )

        return row

    finally:
        conn.close()


def build_profile_context(profile: dict) -> str:
    """
    Supplemental Job Search profile context.

    This does not replace the CV. It has a deliberately small
    influence on semantic ranking.
    """
    sections = []

    for label, field in (
        ("About Me", "about_me"),
        ("Preferences", "preferences"),
        ("Extra Context", "extra_context"),
    ):
        value = (profile.get(field) or "").strip()

        if value:
            sections.append(f"{label}:\n{value}")

    return "\n\n".join(sections)


def clear_resume_scores(resume_hash: str, prompt_id: int = 0):
    """
    Remove previous scores for this exact resume before rescoring.
    This prevents Phase-1 false positives from surviving in the table.
    """
    conn = _get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            DELETE FROM resume_match_scores
            WHERE prompt_id = %s
              AND resume_hash = %s
            """,
            (prompt_id, resume_hash),
        )

        deleted = cur.rowcount
        conn.commit()
        cur.close()

        return deleted

    finally:
        conn.close()


def get_job_columns() -> set[str]:
    conn = _get_conn()

    try:
        cur = conn.cursor()
        cur.execute("SHOW COLUMNS FROM jobs")

        cols = {row[0] for row in cur.fetchall()}

        cur.close()
        return cols

    finally:
        conn.close()


def load_jobs(
    include_reviewed: bool = False,
    prompt_id: int = 0,
    resume_hash: str = "",
    incremental: bool = False,
) -> list[dict]:
    """
    Load jobs for resume matching.

    In incremental mode, jobs already scored for this exact
    Prompt/profile hash are skipped.
    """
    columns = get_job_columns()

    description_col = None

    for candidate in (
        "description",
        "job_description",
        "details",
        "summary",
    ):
        if candidate in columns:
            description_col = candidate
            break

    if not description_col:
        raise RuntimeError(
            "Could not find a job-description column in jobs table."
        )

    conn = _get_conn()

    try:
        cur = conn.cursor(dictionary=True)

        sql = f"""
            SELECT
                j.job_id,
                j.title,
                j.company,
                j.location,
                j.{description_col} AS description
            FROM jobs j
        """

        conditions = []
        params = []

        if not include_reviewed:
            conditions.extend(
                [
                    """
                    j.job_id NOT IN (
                        SELECT job_id FROM favourites
                    )
                    """,
                    """
                    j.job_id NOT IN (
                        SELECT job_id FROM applications
                    )
                    """,
                    """
                    j.job_id NOT IN (
                        SELECT job_id FROM not_interested
                    )
                    """,
                ]
            )

        if incremental:
            conditions.append(
                """
                NOT EXISTS (
                    SELECT 1
                    FROM resume_match_scores rms
                    WHERE rms.job_id = j.job_id
                      AND rms.prompt_id = %s
                      AND rms.resume_hash = %s
                )
                """
            )
            params.extend([prompt_id, resume_hash])

        if conditions:
            sql += "\nWHERE " + "\nAND ".join(conditions)

        cur.execute(sql, params)

        rows = cur.fetchall()
        cur.close()

        return rows

    finally:
        conn.close()


def save_score(
    prompt_id: int,
    resume_hash: str,
    job_id: str,
    match_score: float,
    semantic_score: float,
    title_score: float,
    best_chunk: str,
):
    conn = _get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO resume_match_scores (
                prompt_id,
                resume_hash,
                job_id,
                match_score,
                semantic_score,
                title_score,
                best_resume_chunk,
                model_name,
                analyzed_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())

            ON DUPLICATE KEY UPDATE
                match_score = VALUES(match_score),
                semantic_score = VALUES(semantic_score),
                title_score = VALUES(title_score),
                best_resume_chunk = VALUES(best_resume_chunk),
                model_name = VALUES(model_name),
                analyzed_at = NOW()
            """,
            (
                prompt_id,
                resume_hash,
                job_id,
                round(match_score, 2),
                round(semantic_score, 2),
                round(title_score, 2),
                best_chunk[:4000],
                MODEL_NAME,
            ),
        )

        conn.commit()
        cur.close()

    finally:
        conn.close()


# ============================================================
# Matching
# ============================================================

def build_job_text(job: dict) -> str:
    title = (job.get("title") or "").strip()
    company = (job.get("company") or "").strip()
    description = (job.get("description") or "").strip()

    # Avoid pathological giant descriptions
    description = description[:16000]

    return "\n".join(
        value for value in (title, company, description) if value
    )


def semantic_score_from_similarities(sims: np.ndarray) -> tuple[float, int]:
    best_idx = int(np.argmax(sims))

    count = min(3, len(sims))
    top = np.sort(sims)[-count:]

    # Best matching resume section matters most, but reward several
    # independently relevant areas.
    score = (
        float(top[-1]) * 0.60
        + float(np.mean(top)) * 0.40
    )

    return score, best_idx


def human_score(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * 100.0


def dedupe_results(results: list[dict]) -> list[dict]:
    """
    Collapse duplicate source records for display only.
    All individual records still retain their own DB score.
    """
    output = []
    seen = set()

    for row in results:
        key = (
            re.sub(r"\W+", " ", row["title"].lower()).strip(),
            re.sub(r"\W+", " ", row["company"].lower()).strip(),
            re.sub(r"\W+", " ", row["location"].lower()).strip(),
        )

        if key in seen:
            continue

        seen.add(key)
        output.append(row)

    return output


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--resume",
        help=(
            "Optional resume override (.md/.txt/.pdf/.docx). "
            "If omitted, the active Job Search AI Prompt CV is used."
        ),
    )

    parser.add_argument(
        "--prompt-id",
        type=int,
        help=(
            "Job Search AI Prompt ID to use. "
            "If omitted, the active AI Prompt is used."
        ),
    )

    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of top matches to display",
    )

    parser.add_argument(
        "--include-reviewed",
        action="store_true",
        help="Also score favourites/applied/not-interested jobs",
    )

    parser.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "Only score jobs not already scored for this exact "
            "AI Prompt/profile version."
        ),
    )

    args = parser.parse_args()

    profile_context = ""
    profile_title = ""
    source_description = ""
    prompt_id = 0

    if args.resume:
        resume_path = Path(args.resume).expanduser().resolve()

        if not resume_path.exists():
            print(f"Resume not found: {resume_path}")
            sys.exit(1)

        resume_text = clean_text(load_text(resume_path))

        if len(resume_text) < 200:
            print("Resume text appears too short.")
            sys.exit(1)

        source_description = str(resume_path)

    else:
        try:
            profile = load_ai_profile(args.prompt_id)
        except Exception as exc:
            print(f"Unable to load active Job Search AI profile: {exc}")
            sys.exit(1)

        prompt_id = int(profile.get("id") or 0)

        profile, sync_message = sync_linked_resume_if_changed(
            profile
        )

        if sync_message:
            print(sync_message)

        resume_text = clean_text(profile.get("cv") or "")
        profile_context = clean_text(build_profile_context(profile))
        profile_title = (profile.get("title") or "").strip()

        source_description = (
            f"Job Search AI Prompt"
            + (f": {profile_title}" if profile_title else "")
        )

    # The profile hash deliberately includes supplemental context.
    # Changing preferences/about_me/extra_context therefore creates
    # a distinct scoring profile just like changing the CV does.
    profile_material = resume_text

    if profile_context:
        profile_material += "\n\n--- PROFILE CONTEXT ---\n" + profile_context

    resume_hash = hashlib.sha256(
        profile_material.encode("utf-8")
    ).hexdigest()

    resume_chunks = chunk_resume(resume_text)

    print(f"Profile source: {source_description}")
    print(f"Resume chunks: {len(resume_chunks)}")
    print(
        f"Supplemental profile context: "
        f"{'yes' if profile_context else 'none'}"
    )
    print(f"Model: {MODEL_NAME}")
    print()

    ensure_match_table()

    if args.incremental:
        print(
            "Mode: incremental - previously scored jobs "
            "for this profile will be skipped."
        )
    else:
        deleted = clear_resume_scores(
            resume_hash,
            prompt_id,
        )

        if deleted:
            print(
                f"Removed {deleted} previous scores "
                "for this profile."
            )

    jobs = load_jobs(
        include_reviewed=args.include_reviewed,
        prompt_id=prompt_id,
        resume_hash=resume_hash,
        incremental=args.incremental,
    )

    print(f"Jobs loaded: {len(jobs)}")

    # --------------------------------------------------------
    # Hard eligibility filters
    # --------------------------------------------------------

    eligible = []

    skip_title = 0
    skip_location = 0
    skip_empty = 0

    for job in jobs:
        title = job.get("title") or ""
        location = job.get("location") or ""

        title_ok, title_score, seniority_score, title_reason = evaluate_title(title)

        if not title_ok:
            skip_title += 1
            continue

        location_ok, location_reason = evaluate_location(location)

        if not location_ok:
            skip_location += 1
            continue

        job_text = build_job_text(job)

        if len(job_text) < 40:
            skip_empty += 1
            continue

        job["_title_score"] = title_score
        job["_seniority_score"] = seniority_score
        job["_title_reason"] = title_reason
        job["_location_reason"] = location_reason
        job["_job_text"] = job_text

        eligible.append(job)

    print(f"Eligible leadership jobs: {len(eligible)}")
    print(f"Skipped - wrong title/family: {skip_title}")
    print(f"Skipped - location:           {skip_location}")
    print(f"Skipped - missing content:    {skip_empty}")
    print()

    if not eligible:
        print("No eligible jobs found.")
        return

    # --------------------------------------------------------
    # Embeddings
    # --------------------------------------------------------

    print("Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME)

    print("Embedding resume...")

    resume_embeddings = model.encode(
        resume_chunks,
        normalize_embeddings=True,
        show_progress_bar=True,
        batch_size=32,
    )

    context_embedding = None

    if profile_context:
        print("Embedding Job Search profile context...")

        context_embedding = model.encode(
            [profile_context[:12000]],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]

    print(f"Embedding {len(eligible)} eligible jobs in batches...")

    job_embeddings = model.encode(
        [job["_job_text"] for job in eligible],
        normalize_embeddings=True,
        show_progress_bar=True,
        batch_size=32,
    )

    # resume_chunks x jobs
    similarities = np.matmul(
        resume_embeddings,
        job_embeddings.T,
    )

    # --------------------------------------------------------
    # Rank
    # --------------------------------------------------------

    results = []

    for job_index, job in enumerate(eligible):
        sims = similarities[:, job_index]

        semantic, best_idx = semantic_score_from_similarities(sims)

        # Supplemental native Job Search profile information has a
        # deliberately small influence. The CV remains dominant.
        if context_embedding is not None:
            context_similarity = float(
                np.dot(context_embedding, job_embeddings[job_index])
            )

            semantic_profile = (
                semantic * 0.85
                + context_similarity * 0.15
            )
        else:
            semantic_profile = semantic

        title_score = job["_title_score"]
        seniority_score = job["_seniority_score"]

        combined = (
            semantic_profile * 0.45
            + title_score * 0.35
            + seniority_score * 0.20
        )

        match = human_score(combined)
        semantic_pct = human_score(semantic_profile)
        title_pct = human_score(title_score)

        save_score(
            prompt_id=prompt_id,
            resume_hash=resume_hash,
            job_id=job["job_id"],
            match_score=match,
            semantic_score=semantic_pct,
            title_score=title_pct,
            best_chunk=resume_chunks[best_idx],
        )

        results.append(
            {
                "job_id": job["job_id"],
                "title": job.get("title") or "",
                "company": job.get("company") or "",
                "location": job.get("location") or "",
                "match": match,
                "semantic": semantic_pct,
                "title_score": title_pct,
                "seniority": human_score(seniority_score),
                "reason": job["_title_reason"],
            }
        )

    results.sort(
        key=lambda row: row["match"],
        reverse=True,
    )

    unique_results = dedupe_results(results)

    duplicate_count = len(results) - len(unique_results)

    print()
    print("=" * 118)
    print("TOP RESUME MATCHES - TECHNOLOGY LEADERSHIP")
    print("=" * 118)

    for n, row in enumerate(unique_results[:args.top], 1):
        print(
            f"{n:>2}. "
            f"{row['match']:>5.1f}%  "
            f"{row['title'][:48]:<48} "
            f"{row['company'][:28]:<28} "
            f"{row['location'][:25]}"
        )

    print()
    print(f"Eligible jobs scored: {len(results)}")

    if duplicate_count:
        print(f"Duplicate listings hidden from display: {duplicate_count}")

    print()
    print(
        "Match Score = 45% semantic profile fit + "
        "35% target-role fit + 20% seniority fit."
    )
    print(
        "It is a ranking score, not an ATS score or probability "
        "of receiving an interview."
    )


if __name__ == "__main__":
    main()
