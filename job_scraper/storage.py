"""
MySQL-backed storage engine with deduplication, favourites, and applications.

• Uses a connection pool for thread-safe access.
• INSERT IGNORE ensures duplicate job_ids are silently skipped.
• Graceful error handling when the database is unavailable.
"""

from __future__ import annotations

import io
import csv
import logging
from typing import List, Optional

import mysql.connector
from mysql.connector import pooling

import config
from .models import Job

logger = logging.getLogger(__name__)


# ── Custom exception ──────────────────────────────────────────

class DatabaseUnavailable(Exception):
    """Raised when the MySQL database cannot be reached."""
    pass


# ── Connection pool (thread-safe) ─────────────────────────────

_pool: Optional[pooling.MySQLConnectionPool] = None


def _get_pool() -> pooling.MySQLConnectionPool:
    """Lazy-init a connection pool."""
    global _pool
    if _pool is None:
        try:
            _pool = pooling.MySQLConnectionPool(
                pool_name="jobsearch",
                pool_size=5,
                host=config.DB_HOST,
                port=config.DB_PORT,
                user=config.DB_USER,
                password=config.DB_PASSWORD,
                database=config.DB_NAME,
                charset="utf8mb4",
                collation="utf8mb4_unicode_ci",
                autocommit=True,
            )
        except mysql.connector.Error as exc:
            raise DatabaseUnavailable(str(exc)) from exc
    return _pool


def _get_conn():
    try:
        return _get_pool().get_connection()
    except mysql.connector.Error as exc:
        raise DatabaseUnavailable(str(exc)) from exc


def check_db_connection() -> tuple[bool, str]:
    """
    Test whether the database is reachable.
    Returns (ok, error_message).
    """
    try:
        conn = _get_conn()
        conn.close()
        return True, ""
    except DatabaseUnavailable as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


# ── Region / country pattern mapping for location filtering ────
# Keys are lowercase region labels; values are SQL LIKE patterns.
_REGION_PATTERNS: dict[str, list[str]] = {
    "united kingdom": [
        "%united kingdom%", "%uk%", "%great britain%", "%england%",
        "%scotland%", "%wales%", "%northern ireland%", "%london%",
        "%manchester%", "%birmingham%", "%leeds%", "%glasgow%",
        "%edinburgh%", "%bristol%", "%liverpool%", "%cardiff%",
        "%belfast%", "%newcastle%", "%sheffield%", "%nottingham%",
        "%cambridge%", "%oxford%",
    ],
    "united states": [
        "%united states%", "%, us%", "% us", "%usa%", "%u.s.%",
        "%, al", "%, ak", "%, az", "%, ar", "%, ca", "%, co", "%, ct",
        "%, de", "%, fl", "%, ga", "%, hi", "%, id", "%, il", "%, in",
        "%, ia", "%, ks", "%, ky", "%, la", "%, me", "%, md", "%, ma",
        "%, mi", "%, mn", "%, ms", "%, mo", "%, mt", "%, ne", "%, nv",
        "%, nh", "%, nj", "%, nm", "%, ny", "%, nc", "%, nd", "%, oh",
        "%, ok", "%, or", "%, pa", "%, ri", "%, sc", "%, sd", "%, tn",
        "%, tx", "%, ut", "%, vt", "%, va", "%, wa", "%, wv", "%, wi", "%, wy",
        "%alabama%", "%alaska%", "%arizona%", "%arkansas%", "%california%",
        "%colorado%", "%connecticut%", "%delaware%", "%florida%", "%georgia%",
        "%hawaii%", "%idaho%", "%illinois%", "%indiana%", "%iowa%",
        "%kansas%", "%kentucky%", "%louisiana%", "%maine%", "%maryland%",
        "%massachusetts%", "%michigan%", "%minnesota%", "%mississippi%",
        "%missouri%", "%montana%", "%nebraska%", "%nevada%",
        "%new hampshire%", "%new jersey%", "%new mexico%", "%new york%",
        "%north carolina%", "%north dakota%", "%ohio%", "%oklahoma%",
        "%oregon%", "%pennsylvania%", "%rhode island%", "%south carolina%",
        "%south dakota%", "%tennessee%", "%texas%", "%utah%", "%vermont%",
        "%virginia%", "%washington%", "%west virginia%", "%wisconsin%", "%wyoming%",
        "%san francisco%", "%los angeles%", "%chicago%", "%houston%",
        "%phoenix%", "%seattle%", "%denver%", "%boston%", "%austin%",
        "%portland%", "%atlanta%", "%miami%", "%dallas%", "%san diego%",
        "%san jose%", "%philadelphia%", "%minneapolis%",
    ],
    "canada": [
        "%canada%", "%, ca%",
        "%toronto%", "%vancouver%", "%montreal%", "%ottawa%",
        "%calgary%", "%edmonton%", "%winnipeg%", "%quebec%",
        "%ontario%", "%british columbia%", "%alberta%", "%nova scotia%",
    ],
    "germany": [
        "%germany%", "%deutschland%", "%berlin%", "%munich%",
        "%münchen%", "%hamburg%", "%frankfurt%", "%cologne%",
        "%köln%", "%düsseldorf%", "%stuttgart%",
    ],
    "france": [
        "%france%", "%paris%", "%lyon%", "%marseille%",
        "%toulouse%", "%bordeaux%", "%lille%",
    ],
    "netherlands": [
        "%netherlands%", "%holland%", "%amsterdam%",
        "%rotterdam%", "%the hague%", "%utrecht%", "%eindhoven%",
    ],
    "ireland": [
        "%ireland%", "%dublin%", "%cork%", "%galway%", "%limerick%",
    ],
    "australia": [
        "%australia%", "%sydney%", "%melbourne%", "%brisbane%",
        "%perth%", "%adelaide%", "%canberra%",
    ],
    "india": [
        "%india%", "%bangalore%", "%bengaluru%", "%mumbai%",
        "%delhi%", "%hyderabad%", "%chennai%", "%pune%",
        "%kolkata%", "%noida%", "%gurgaon%", "%gurugram%",
    ],
    "spain": [
        "%spain%", "%españa%", "%madrid%", "%barcelona%",
        "%valencia%", "%seville%", "%malaga%",
    ],
    "italy": [
        "%italy%", "%italia%", "%rome%", "%roma%",
        "%milan%", "%milano%", "%turin%", "%naples%",
    ],
    "sweden": [
        "%sweden%", "%stockholm%", "%gothenburg%", "%malmö%",
    ],
    "switzerland": [
        "%switzerland%", "%zürich%", "%zurich%", "%geneva%",
        "%genève%", "%bern%", "%basel%",
    ],
    "singapore": ["%singapore%"],
    "japan": ["%japan%", "%tokyo%", "%osaka%", "%kyoto%"],
    "brazil": ["%brazil%", "%são paulo%", "%rio de janeiro%"],
    "mexico": ["%mexico%", "%ciudad de méxico%", "%guadalajara%", "%monterrey%"],
    "poland": ["%poland%", "%warsaw%", "%krakow%", "%kraków%", "%wroclaw%"],
    "portugal": ["%portugal%", "%lisbon%", "%lisboa%", "%porto%"],
    "remote / anywhere": ["%remote%", "%anywhere%", "%worldwide%", "%global%"],
    "europe": [
        "%europe%", "%eu %", "% eu", "%european union%",
        "%emea%",
    ],
}


class JobStorage:
    """Read / write job listings to MySQL with dedup."""

    # ══════════════════════════════════════════════════════════════
    #  JOBS
    # ══════════════════════════════════════════════════════════════

    def save_jobs(self, jobs: List[Job]) -> int:
        """
        Insert new jobs into the database. Deduplication is enforced by the
        unique key on job_id (hash of source + url). INSERT IGNORE skips rows
        that would violate the key, so duplicates are never stored.
        Returns the number of jobs actually written (new only).
        """
        if not jobs:
            return 0

        sql = """
            INSERT IGNORE INTO jobs
                (job_id, title, company, location, description, url, source,
                 remote, salary_min, salary_max, salary_currency, job_type,
                 experience_level, date_posted, date_scraped, tags, company_logo)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s)
        """

        rows = []
        for j in jobs:
            rows.append((
                j.job_id,
                j.title,
                j.company,
                j.location,
                j.description,
                j.url,
                j.source,
                j.remote,
                j.salary_min if j.salary_min else None,
                j.salary_max if j.salary_max else None,
                j.salary_currency,
                j.job_type,
                j.experience_level,
                j.date_posted,
                j.date_scraped,
                j.tags,
                j.company_logo,
            ))

        conn = _get_conn()
        try:
            cursor = conn.cursor()
            saved = 0
            for row in rows:
                cursor.execute(sql, row)
                saved += cursor.rowcount
            cursor.close()
            return saved
        finally:
            conn.close()

    def load_all(self) -> list[dict]:
        """Load every job from the database."""
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM jobs ORDER BY date_scraped DESC")
            rows = cursor.fetchall()
            cursor.close()
            return self._normalize_rows(rows)
        finally:
            conn.close()

    def search(
        self,
        query: str = "",
        source: str = "",
        remote: str = "",
        job_type: str = "",
        salary_min: Optional[float] = None,
        posted_in_last_days: Optional[int] = None,
        sort_by: str = "date_posted",
        ascending: bool = False,
        exclude_not_interested: bool = True,
        region: str = "",
    ) -> list[dict]:
        """Filter and sort stored jobs using SQL."""
        conditions = []
        params: list = []

        if posted_in_last_days is not None and posted_in_last_days > 0:
            conditions.append(
                "(CASE WHEN `date_posted` REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
                " THEN CAST(`date_posted` AS DATE)"
                " ELSE DATE(`date_scraped`) END) >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"
            )
            params.append(posted_in_last_days)

        if query:
            conditions.append(
                "MATCH(title, company, description, tags, location) AGAINST(%s IN BOOLEAN MODE)"
            )
            terms = query.strip().split()
            boolean_query = " ".join(f"+{t}*" for t in terms if t)
            params.append(boolean_query)

        if source:
            conditions.append("source = %s")
            params.append(source)

        if remote and remote != "Any":
            conditions.append("remote = %s")
            params.append(remote)

        if job_type:
            conditions.append("job_type LIKE %s")
            params.append(f"%{job_type}%")

        if salary_min is not None:
            conditions.append("(salary_min IS NOT NULL AND salary_min >= %s)")
            params.append(salary_min)

        if exclude_not_interested:
            conditions.append(
                "j.job_id NOT IN (SELECT ni.job_id FROM not_interested ni)"
            )

        if region:
            region_patterns = _REGION_PATTERNS.get(region.lower(), [])
            if region_patterns:
                like_clauses = " OR ".join(["LOWER(j.location) LIKE %s"] * len(region_patterns))
                conditions.append(f"({like_clauses})")
                params.extend(region_patterns)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        allowed_sort = {
            "date_scraped", "title", "company", "source",
            "salary_min", "salary_max", "date_posted",
        }
        if sort_by not in allowed_sort:
            sort_by = "date_posted"
        direction = "ASC" if ascending else "DESC"

        # date_posted is VARCHAR; use CASE so valid ISO dates sort correctly and empty/invalid fall back to date_scraped
        if sort_by == "date_posted":
            order_expr = (
                "CASE WHEN `date_posted` REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN `date_posted` ELSE '0000-00-00' END"
                f" {direction}, `date_scraped` {direction}"
            )
        else:
            order_expr = f"`{sort_by}` {direction}"

        sql = f"SELECT j.* FROM jobs j{where} ORDER BY {order_expr}"

        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return self._normalize_rows(rows)
        finally:
            conn.close()

    def count(self) -> int:
        """Return total number of stored jobs."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM jobs")
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
        finally:
            conn.close()

    def get_job(self, job_id: str) -> Optional[dict]:
        """Retrieve a single job by its ID, including favourite/applied/not-interested status."""
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """SELECT j.*,
                          IF(f.job_id IS NOT NULL, 1, 0) AS is_favourite,
                          IF(a.job_id IS NOT NULL, 1, 0) AS is_applied,
                          IF(ni.job_id IS NOT NULL, 1, 0) AS is_not_interested,
                          a.applied_at,
                          a.notes AS application_notes
                   FROM jobs j
                   LEFT JOIN favourites f ON f.job_id = j.job_id
                   LEFT JOIN applications a ON a.job_id = j.job_id
                   LEFT JOIN not_interested ni ON ni.job_id = j.job_id
                   WHERE j.job_id = %s""",
                (job_id,),
            )
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            return self._normalize_row(row)
        finally:
            conn.close()

    def get_sources(self) -> list[str]:
        """Return distinct source names from jobs."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT source FROM jobs ORDER BY source")
            sources = [r[0] for r in cursor.fetchall()]
            cursor.close()
            return sources
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Return summary statistics."""
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)

            cursor.execute("SELECT COUNT(*) AS total FROM jobs")
            total = cursor.fetchone()["total"]

            if total == 0:
                cursor.execute("SELECT COUNT(*) AS cnt FROM notes")
                notes_count = cursor.fetchone()["cnt"]
                cursor.execute("SELECT COUNT(*) AS cnt FROM ai_prompts")
                ai_prompts_count = cursor.fetchone()["cnt"]
                cursor.close()
                return {
                    "total": 0, "sources": {}, "remote_count": 0,
                    "job_types": {}, "favourite_count": 0, "applied_count": 0,
                    "notes_count": notes_count, "ai_prompts_count": ai_prompts_count,
                }

            cursor.execute("SELECT source, COUNT(*) AS cnt FROM jobs GROUP BY source")
            sources = {r["source"]: r["cnt"] for r in cursor.fetchall()}

            cursor.execute("SELECT COUNT(*) AS cnt FROM jobs WHERE LOWER(remote) = 'remote'")
            remote_count = cursor.fetchone()["cnt"]

            cursor.execute(
                "SELECT job_type, COUNT(*) AS cnt FROM jobs "
                "WHERE job_type != '' GROUP BY job_type"
            )
            job_types = {r["job_type"]: r["cnt"] for r in cursor.fetchall()}

            cursor.execute("SELECT COUNT(*) AS cnt FROM favourites")
            favourite_count = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) AS cnt FROM applications")
            applied_count = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) AS cnt FROM notes")
            notes_count = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) AS cnt FROM ai_prompts")
            ai_prompts_count = cursor.fetchone()["cnt"]

            cursor.close()
            return {
                "total": total,
                "sources": sources,
                "remote_count": remote_count,
                "job_types": job_types,
                "favourite_count": favourite_count,
                "applied_count": applied_count,
                "notes_count": notes_count,
                "ai_prompts_count": ai_prompts_count,
            }
        finally:
            conn.close()

    def export_csv_string(self) -> str:
        """Export all jobs as a CSV string (for download)."""
        jobs = self.load_all()
        if not jobs:
            return ",".join(Job.csv_columns()) + "\n"

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=Job.csv_columns(), extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            writer.writerow(job)
        return output.getvalue()

    # ══════════════════════════════════════════════════════════════
    #  FAVOURITES
    # ══════════════════════════════════════════════════════════════

    def add_favourite(self, job_id: str) -> bool:
        """Add a job to favourites. Returns True if newly added."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT IGNORE INTO favourites (job_id) VALUES (%s)", (job_id,)
            )
            added = cursor.rowcount > 0
            cursor.close()
            return added
        finally:
            conn.close()

    def remove_favourite(self, job_id: str) -> bool:
        """Remove a job from favourites. Returns True if it was removed."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM favourites WHERE job_id = %s", (job_id,))
            removed = cursor.rowcount > 0
            cursor.close()
            return removed
        finally:
            conn.close()

    def is_favourite(self, job_id: str) -> bool:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM favourites WHERE job_id = %s", (job_id,))
            result = cursor.fetchone()
            cursor.close()
            return result is not None
        finally:
            conn.close()

    def get_favourites(
        self,
        sort_by: str = "created_at",
        ascending: bool = False,
    ) -> list[dict]:
        """Return all favourite jobs with their data."""
        allowed_sort = {"created_at", "title", "company", "date_scraped"}
        if sort_by not in allowed_sort:
            sort_by = "created_at"
        direction = "ASC" if ascending else "DESC"

        sql = f"""
            SELECT j.*, f.created_at AS favourited_at,
                   IF(a.job_id IS NOT NULL, 1, 0) AS is_applied
            FROM favourites f
            JOIN jobs j ON j.job_id = f.job_id
            LEFT JOIN applications a ON a.job_id = j.job_id
            ORDER BY `{sort_by}` {direction}
        """
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            rows = cursor.fetchall()
            cursor.close()
            result = self._normalize_rows(rows)
            for r in result:
                r["is_favourite"] = 1
            return result
        finally:
            conn.close()

    def get_favourite_job_ids(self) -> set[str]:
        """Return a set of all favourited job_ids (for bulk status checks)."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT job_id FROM favourites")
            ids = {r[0] for r in cursor.fetchall()}
            cursor.close()
            return ids
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    #  APPLICATIONS
    # ══════════════════════════════════════════════════════════════

    def add_application(self, job_id: str, notes: str = "") -> bool:
        """Mark a job as applied. Returns True if newly added."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT IGNORE INTO applications (job_id, notes) VALUES (%s, %s)",
                (job_id, notes),
            )
            added = cursor.rowcount > 0
            cursor.close()
            return added
        finally:
            conn.close()

    def remove_application(self, job_id: str) -> bool:
        """Un-mark a job as applied. Returns True if it was removed."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM applications WHERE job_id = %s", (job_id,))
            removed = cursor.rowcount > 0
            cursor.close()
            return removed
        finally:
            conn.close()

    def update_application_notes(self, job_id: str, notes: str) -> bool:
        """Update notes on an existing application."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE applications SET notes = %s WHERE job_id = %s",
                (notes, job_id),
            )
            updated = cursor.rowcount > 0
            cursor.close()
            return updated
        finally:
            conn.close()

    def is_applied(self, job_id: str) -> bool:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM applications WHERE job_id = %s", (job_id,))
            result = cursor.fetchone()
            cursor.close()
            return result is not None
        finally:
            conn.close()

    def get_applications(
        self,
        sort_by: str = "applied_at",
        ascending: bool = False,
    ) -> list[dict]:
        """Return all applied jobs with their data."""
        allowed_sort = {"applied_at", "title", "company", "date_scraped"}
        if sort_by not in allowed_sort:
            sort_by = "applied_at"
        direction = "ASC" if ascending else "DESC"

        sql = f"""
            SELECT j.*, a.applied_at, a.notes AS application_notes,
                   IF(f.job_id IS NOT NULL, 1, 0) AS is_favourite
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            LEFT JOIN favourites f ON f.job_id = j.job_id
            ORDER BY `{sort_by}` {direction}
        """
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            rows = cursor.fetchall()
            cursor.close()
            result = self._normalize_rows(rows)
            for r in result:
                r["is_applied"] = 1
            return result
        finally:
            conn.close()

    def get_applied_job_ids(self) -> set[str]:
        """Return a set of all applied job_ids (for bulk status checks)."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT job_id FROM applications")
            ids = {r[0] for r in cursor.fetchall()}
            cursor.close()
            return ids
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    #  NOT INTERESTED
    # ══════════════════════════════════════════════════════════════

    def add_not_interested(self, job_id: str) -> bool:
        """Mark a job as not interested. Returns True if newly added."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT IGNORE INTO not_interested (job_id) VALUES (%s)", (job_id,)
            )
            added = cursor.rowcount > 0
            cursor.close()
            return added
        finally:
            conn.close()

    def remove_not_interested(self, job_id: str) -> bool:
        """Remove not interested status. Returns True if it was removed."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM not_interested WHERE job_id = %s", (job_id,))
            removed = cursor.rowcount > 0
            cursor.close()
            return removed
        finally:
            conn.close()

    def get_not_interested_job_ids(self) -> set[str]:
        """Return a set of all not-interested job_ids."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT job_id FROM not_interested")
            ids = {r[0] for r in cursor.fetchall()}
            cursor.close()
            return ids
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    #  NOTES
    # ══════════════════════════════════════════════════════════════

    def create_note(self, title: str, body: str) -> int:
        """Create a new note. Returns the new note's id."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO notes (title, body) VALUES (%s, %s)",
                (title, body),
            )
            note_id = cursor.lastrowid
            cursor.close()
            return note_id
        finally:
            conn.close()

    def update_note(self, note_id: int, title: str, body: str) -> bool:
        """Update an existing note. Returns True if the note was found and updated."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE notes SET title = %s, body = %s WHERE id = %s",
                (title, body, note_id),
            )
            updated = cursor.rowcount > 0
            cursor.close()
            return updated
        finally:
            conn.close()

    def delete_note(self, note_id: int) -> bool:
        """Delete a note. Returns True if it was removed."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM notes WHERE id = %s", (note_id,))
            removed = cursor.rowcount > 0
            cursor.close()
            return removed
        finally:
            conn.close()

    def get_note(self, note_id: int) -> Optional[dict]:
        """Retrieve a single note by id."""
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM notes WHERE id = %s", (note_id,))
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            return self._normalize_note(row)
        finally:
            conn.close()

    def get_notes(
        self,
        query: str = "",
        sort_by: str = "updated_at",
        ascending: bool = False,
    ) -> list[dict]:
        """Return all notes, optionally filtered by search query."""
        conditions = []
        params: list = []

        if query:
            conditions.append(
                "MATCH(title, body) AGAINST(%s IN BOOLEAN MODE)"
            )
            terms = query.strip().split()
            boolean_query = " ".join(f"+{t}*" for t in terms if t)
            params.append(boolean_query)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        allowed_sort = {"created_at", "updated_at", "title"}
        if sort_by not in allowed_sort:
            sort_by = "updated_at"
        direction = "ASC" if ascending else "DESC"

        sql = f"SELECT * FROM notes{where} ORDER BY `{sort_by}` {direction}"
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return [self._normalize_note(r) for r in rows]
        finally:
            conn.close()

    def count_notes(self) -> int:
        """Return total number of notes."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM notes")
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
        finally:
            conn.close()

    @staticmethod
    def _normalize_note(row: dict) -> dict:
        """Convert a notes row to JSON-safe values, keeping the id."""
        out = {}
        for k, v in row.items():
            if v is None:
                out[k] = ""
            elif hasattr(v, "isoformat"):
                out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(v, (int, float)):
                out[k] = v
            else:
                out[k] = str(v)
        return out

    # ══════════════════════════════════════════════════════════════
    #  AI ANALYSES
    # ══════════════════════════════════════════════════════════════

    def save_ai_analysis(
        self, job_id: str, prompt_id: int, model: str, result: dict
    ) -> int:
        """
        Upsert an AI analysis result for a (job, prompt) pair.
        Re-running an analysis overwrites the previous result and updates
        created_at to reflect when the latest run completed.
        Returns the row id.
        """
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO ai_analyses (job_id, prompt_id, model, result)
                   VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       model      = VALUES(model),
                       result     = VALUES(result),
                       created_at = NOW()""",
                (job_id, prompt_id, model, json.dumps(result)),
            )
            # Fetch id reliably after upsert
            cursor.execute(
                "SELECT id FROM ai_analyses WHERE job_id=%s AND prompt_id=%s",
                (job_id, prompt_id),
            )
            row = cursor.fetchone()
            analysis_id = row[0] if row else 0
            cursor.close()
            return analysis_id
        finally:
            conn.close()

    def get_ai_analysis(self, analysis_id: int) -> Optional[dict]:
        """Retrieve a single AI analysis by id."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM ai_analyses WHERE id=%s", (analysis_id,))
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            item = self._normalize_note(row)
            try:
                item["result"] = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
            except (json.JSONDecodeError, TypeError):
                item["result"] = {}
            return item
        finally:
            conn.close()

    def get_ai_analyses_for_job(self, job_id: str) -> list[dict]:
        """Return all AI analyses for a given job, newest first."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """SELECT a.*, p.title AS prompt_title, p.model AS prompt_model
                   FROM ai_analyses a
                   LEFT JOIN ai_prompts p ON p.id = a.prompt_id
                   WHERE a.job_id = %s
                   ORDER BY a.created_at DESC""",
                (job_id,),
            )
            rows = cursor.fetchall()
            cursor.close()
            result = []
            for row in rows:
                item = self._normalize_note(row)
                try:
                    item["result"] = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
                except (json.JSONDecodeError, TypeError):
                    item["result"] = {}
                result.append(item)
            return result
        finally:
            conn.close()

    def get_ai_analyses_list(
        self,
        query: str = "",
        min_score: int = 0,
        recommendations: list | None = None,
        prompt_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        Return a paginated list of AI analyses joined with job data, newest first.
        Returns (rows, total_count).
        """
        import json as _json_mod

        where_parts: list[str] = []
        params: list = []

        if min_score and min_score > 0:
            where_parts.append(
                "CAST(JSON_EXTRACT(a.result, '$.match_score') AS UNSIGNED) >= %s"
            )
            params.append(min_score)

        if recommendations:
            ph = ",".join(["%s"] * len(recommendations))
            where_parts.append(
                f"JSON_UNQUOTE(JSON_EXTRACT(a.result, '$.recommendation')) IN ({ph})"
            )
            params.extend(recommendations)

        if prompt_id:
            where_parts.append("a.prompt_id = %s")
            params.append(prompt_id)

        if query:
            q = f"%{query}%"
            where_parts.append(
                "(LOWER(j.title) LIKE LOWER(%s)"
                " OR LOWER(j.company) LIKE LOWER(%s)"
                " OR LOWER(CONVERT(a.result USING utf8mb4)) LIKE LOWER(%s))"
            )
            params.extend([q, q, q])

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        base_from = f"""
            FROM ai_analyses a
            JOIN jobs j ON a.job_id = j.job_id
            LEFT JOIN ai_prompts p      ON p.id       = a.prompt_id
            LEFT JOIN favourites fav    ON fav.job_id  = j.job_id
            LEFT JOIN applications app  ON app.job_id  = j.job_id
            LEFT JOIN not_interested ni ON ni.job_id   = j.job_id
            {where_sql}
        """

        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)

            cursor.execute(f"SELECT COUNT(*) AS cnt {base_from}", params)
            total: int = cursor.fetchone()["cnt"]

            cursor.execute(
                f"""SELECT
                        a.id              AS analysis_id,
                        a.job_id,
                        a.prompt_id,
                        a.model           AS analysis_model,
                        a.result,
                        a.created_at      AS analysed_at,
                        j.title,
                        j.company,
                        j.location,
                        j.remote,
                        j.job_type,
                        j.salary_min,
                        j.salary_max,
                        j.salary_currency,
                        j.url,
                        j.source,
                        j.description                                AS job_description_raw,
                        IF(fav.job_id IS NOT NULL, 1, 0)             AS is_favourite,
                        IF(app.job_id IS NOT NULL, 1, 0)             AS is_applied,
                        IF(ni.job_id  IS NOT NULL, 1, 0)             AS is_not_interested,
                        j.company_logo,
                        p.title           AS prompt_title
                    {base_from}
                    ORDER BY a.created_at DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            cursor.close()

            results = []
            for row in rows:
                item: dict = {}
                for k, v in row.items():
                    if k == "result":
                        continue
                    if v is None:
                        item[k] = ""
                    elif hasattr(v, "isoformat"):
                        item[k] = v.strftime("%Y-%m-%d %H:%M:%S")
                    elif isinstance(v, (int, float)):
                        item[k] = v
                    else:
                        item[k] = str(v)
                raw = row.get("result", "{}")
                try:
                    item["result"] = (
                        _json_mod.loads(raw) if isinstance(raw, str) else (raw or {})
                    )
                except Exception:
                    item["result"] = {}
                results.append(item)

            return results, total
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    #  AI PROMPTS
    # ══════════════════════════════════════════════════════════════

    def create_ai_prompt(
        self,
        title: str,
        model: str,
        cv: str,
        about_me: str,
        preferences: str,
        extra_context: str,
        is_active: bool = False,
    ) -> int:
        """Create a new AI prompt configuration. Returns the new id."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            if is_active:
                cursor.execute("UPDATE ai_prompts SET is_active = 0")
            cursor.execute(
                """INSERT INTO ai_prompts
                   (title, model, cv, about_me, preferences, extra_context, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (title, model, cv, about_me, preferences, extra_context, int(is_active)),
            )
            prompt_id = cursor.lastrowid
            cursor.close()
            return prompt_id
        finally:
            conn.close()

    def get_ai_prompts(self) -> list[dict]:
        """Return all AI prompt configurations, active first then newest."""
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM ai_prompts ORDER BY is_active DESC, updated_at DESC"
            )
            rows = cursor.fetchall()
            cursor.close()
            return [self._normalize_note(r) for r in rows]
        finally:
            conn.close()

    def get_ai_prompt(self, prompt_id: int) -> Optional[dict]:
        """Retrieve a single AI prompt by id."""
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM ai_prompts WHERE id = %s", (prompt_id,))
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            return self._normalize_note(row)
        finally:
            conn.close()

    def get_active_ai_prompt(self) -> Optional[dict]:
        """Return the currently active AI prompt, or None."""
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM ai_prompts WHERE is_active = 1 LIMIT 1")
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            return self._normalize_note(row)
        finally:
            conn.close()

    def update_ai_prompt(
        self,
        prompt_id: int,
        title: str,
        model: str,
        cv: str,
        about_me: str,
        preferences: str,
        extra_context: str,
        is_active: bool = False,
    ) -> bool:
        """Update an existing AI prompt. Returns True if found and updated."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            if is_active:
                cursor.execute(
                    "UPDATE ai_prompts SET is_active = 0 WHERE id != %s", (prompt_id,)
                )
            cursor.execute(
                """UPDATE ai_prompts
                   SET title=%s, model=%s, cv=%s, about_me=%s, preferences=%s,
                       extra_context=%s, is_active=%s
                   WHERE id=%s""",
                (title, model, cv, about_me, preferences, extra_context, int(is_active), prompt_id),
            )
            updated = cursor.rowcount > 0
            cursor.close()
            return updated
        finally:
            conn.close()

    def set_active_ai_prompt(self, prompt_id: int) -> bool:
        """Mark one prompt as active and clear all others. Returns True if found."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE ai_prompts SET is_active = 0")
            cursor.execute(
                "UPDATE ai_prompts SET is_active = 1 WHERE id = %s", (prompt_id,)
            )
            updated = cursor.rowcount > 0
            cursor.close()
            return updated
        finally:
            conn.close()

    def delete_ai_prompt(self, prompt_id: int) -> bool:
        """Delete an AI prompt. Returns True if removed."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ai_prompts WHERE id = %s", (prompt_id,))
            removed = cursor.rowcount > 0
            cursor.close()
            return removed
        finally:
            conn.close()

    def count_ai_prompts(self) -> int:
        """Return total number of AI prompt configurations."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM ai_prompts")
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    #  SAVED SEARCHES
    # ══════════════════════════════════════════════════════════════

    def create_saved_search(self, name: str, params: dict) -> int:
        """Save a search configuration. Returns the new id."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO saved_searches (name, params) VALUES (%s, %s)",
                (name, json.dumps(params)),
            )
            search_id = cursor.lastrowid
            cursor.close()
            return search_id
        finally:
            conn.close()

    def get_saved_searches(self) -> list[dict]:
        """Return all saved searches ordered by most recent first."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM saved_searches ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            cursor.close()
            result = []
            for row in rows:
                item = self._normalize_note(row)  # reuse datetime normaliser
                # Parse the JSON params back into a dict
                try:
                    item["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
                except (json.JSONDecodeError, TypeError):
                    item["params"] = {}
                result.append(item)
            return result
        finally:
            conn.close()

    def get_saved_search(self, search_id: int) -> Optional[dict]:
        """Retrieve a single saved search by id."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM saved_searches WHERE id = %s", (search_id,))
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            item = self._normalize_note(row)
            try:
                item["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
            except (json.JSONDecodeError, TypeError):
                item["params"] = {}
            return item
        finally:
            conn.close()

    def update_saved_search(self, search_id: int, name: str, params: dict) -> bool:
        """Update an existing saved search. Returns True if found and updated."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE saved_searches SET name = %s, params = %s WHERE id = %s",
                (name, json.dumps(params), search_id),
            )
            updated = cursor.rowcount > 0
            cursor.close()
            return updated
        finally:
            conn.close()

    def delete_saved_search(self, search_id: int) -> bool:
        """Delete a saved search. Returns True if removed."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM saved_searches WHERE id = %s", (search_id,))
            removed = cursor.rowcount > 0
            cursor.close()
            return removed
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    #  SAVED BOARD SEARCHES
    # ══════════════════════════════════════════════════════════════

    def create_saved_board_search(self, name: str, params: dict) -> int:
        """Save a board filter configuration. Returns the new id."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO saved_board_searches (name, params) VALUES (%s, %s)",
                (name, json.dumps(params)),
            )
            search_id = cursor.lastrowid
            cursor.close()
            return search_id
        finally:
            conn.close()

    def get_saved_board_searches(self) -> list[dict]:
        """Return all saved board searches ordered by most recent first."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM saved_board_searches ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            cursor.close()
            result = []
            for row in rows:
                item = self._normalize_note(row)
                try:
                    item["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
                except (json.JSONDecodeError, TypeError):
                    item["params"] = {}
                result.append(item)
            return result
        finally:
            conn.close()

    def get_saved_board_search(self, search_id: int) -> Optional[dict]:
        """Retrieve a single saved board search by id."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM saved_board_searches WHERE id = %s", (search_id,))
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            item = self._normalize_note(row)
            try:
                item["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
            except (json.JSONDecodeError, TypeError):
                item["params"] = {}
            return item
        finally:
            conn.close()

    def update_saved_board_search(self, search_id: int, name: str, params: dict) -> bool:
        """Update an existing saved board search. Returns True if found and updated."""
        import json
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE saved_board_searches SET name = %s, params = %s WHERE id = %s",
                (name, json.dumps(params), search_id),
            )
            updated = cursor.rowcount > 0
            cursor.close()
            return updated
        finally:
            conn.close()

    def delete_saved_board_search(self, search_id: int) -> bool:
        """Delete a saved board search. Returns True if removed."""
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM saved_board_searches WHERE id = %s", (search_id,))
            removed = cursor.rowcount > 0
            cursor.close()
            return removed
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    #  BULK STATUS
    # ══════════════════════════════════════════════════════════════

    def get_job_statuses(self, job_ids: list[str]) -> dict:
        """
        For a list of job_ids, return which are favourited, applied, and/or not interested.
        Returns {job_id: {"is_favourite": bool, "is_applied": bool, "is_not_interested": bool}}.
        """
        if not job_ids:
            return {}

        fav_ids = self.get_favourite_job_ids()
        app_ids = self.get_applied_job_ids()
        ni_ids = self.get_not_interested_job_ids()

        return {
            jid: {
                "is_favourite": jid in fav_ids,
                "is_applied": jid in app_ids,
                "is_not_interested": jid in ni_ids,
            }
            for jid in job_ids
        }

    # ══════════════════════════════════════════════════════════════
    #  INTERNAL HELPERS
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _normalize_row(row: dict) -> dict:
        """Convert MySQL row types to JSON-safe values."""
        out = {}
        for k, v in row.items():
            if k == "id":
                continue
            if v is None:
                out[k] = ""
            elif hasattr(v, "isoformat"):
                out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(v, (int, float)):
                out[k] = v
            else:
                out[k] = str(v)
        return out

    @staticmethod
    def _normalize_rows(rows: list[dict]) -> list[dict]:
        return [JobStorage._normalize_row(r) for r in rows]
