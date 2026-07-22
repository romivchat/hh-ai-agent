import os
import json
import sqlite3
from typing import Optional


DB_PATH = os.path.join(os.path.dirname(__file__), "agent.db")

PENDING = "pending"
APPLYING = "applying"
APPLIED = "applied"
SKIPPED = "skipped"
FILTERED = "filtered"

VACANCY_FIELDS = (
    "id, title, url, cover_letter, status, description, analysis_json, "
    "warnings_json, strengths_json, letter_version, letter_edited, "
    "created_at, updated_at"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applied_jobs (
                id TEXT PRIMARY KEY,
                title TEXT,
                url TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vacancies (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                cover_letter TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'applying', 'applied', 'skipped', 'filtered')
                ),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vacancies_status ON vacancies(status)"
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(vacancies)").fetchall()
        }
        migrations = {
            "description": "ALTER TABLE vacancies ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            "analysis_json": "ALTER TABLE vacancies ADD COLUMN analysis_json TEXT NOT NULL DEFAULT '{}'",
            "warnings_json": "ALTER TABLE vacancies ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'",
            "strengths_json": "ALTER TABLE vacancies ADD COLUMN strengths_json TEXT NOT NULL DEFAULT '[]'",
            "letter_version": "ALTER TABLE vacancies ADD COLUMN letter_version INTEGER NOT NULL DEFAULT 1",
            "letter_edited": "ALTER TABLE vacancies ADD COLUMN letter_edited INTEGER NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)

        # Старые письма, не похожие на известные шаблоны генератора, считаем ручными.
        conn.execute(
            """
            UPDATE vacancies
            SET letter_edited = 1
            WHERE status = 'pending'
              AND letter_version = 1
              AND cover_letter IS NOT NULL
              AND cover_letter NOT LIKE '%две ключевые задачи%'
              AND cover_letter NOT LIKE '%Две задачи вакансии%'
              AND cover_letter NOT LIKE '%Для этой роли особенно важны две задачи%'
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                msg_id TEXT PRIMARY KEY,
                chat_id TEXT,
                text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Сохраняем совместимость с базой исходного проекта.
        conn.execute(
            """
            INSERT OR IGNORE INTO vacancies (id, title, url, status)
            SELECT id, COALESCE(title, ''), COALESCE(url, ''), 'applied'
            FROM applied_jobs
            """
        )

        # Если процесс оборвался во время отправки, решение снова должен принять владелец.
        conn.execute(
            """
            UPDATE vacancies
            SET status = 'pending', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'applying'
            """
        )


def is_job_processed(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM vacancies WHERE id = ?", (job_id,)
        ).fetchone()
    return row is not None


def is_job_applied(job_id: str) -> bool:
    """Совместимость со старым именем функции."""
    return is_job_processed(job_id)


def count_pending_jobs() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM vacancies WHERE status = ?", (PENDING,)
        ).fetchone()
    return int(row["count"])


def add_pending_job(
    job_id: str,
    title: str,
    url: str,
    cover_letter: str,
    max_pending: int,
    description: str = "",
    analysis: Optional[dict] = None,
    warnings: Optional[list[str]] = None,
    strengths: Optional[list[str]] = None,
    letter_version: int = 1,
) -> bool:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pending_count = conn.execute(
            "SELECT COUNT(*) AS count FROM vacancies WHERE status = ?", (PENDING,)
        ).fetchone()["count"]
        if pending_count >= max_pending:
            return False

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO vacancies (
                id, title, url, cover_letter, status, description,
                analysis_json, warnings_json, strengths_json, letter_version,
                letter_edited
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                job_id,
                title,
                url,
                cover_letter,
                PENDING,
                description,
                json.dumps(analysis or {}, ensure_ascii=False),
                json.dumps(warnings or [], ensure_ascii=False),
                json.dumps(strengths or [], ensure_ascii=False),
                letter_version,
            ),
        )
        return cursor.rowcount == 1


def add_filtered_job(job_id: str, title: str, url: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO vacancies (id, title, url, status)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, title, url, FILTERED),
        )
    return cursor.rowcount == 1


def get_job(job_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT {VACANCY_FIELDS}
            FROM vacancies
            WHERE id = ?
            """.format(VACANCY_FIELDS=VACANCY_FIELDS),
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def list_pending_jobs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT {VACANCY_FIELDS}
            FROM vacancies
            WHERE status = ?
            ORDER BY created_at, id
            """.format(VACANCY_FIELDS=VACANCY_FIELDS),
            (PENDING,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_cover_letter(
    job_id: str,
    cover_letter: str,
    manual: bool = True,
) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE vacancies
            SET cover_letter = ?, letter_edited = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (cover_letter, int(manual), job_id, PENDING),
        )
    return cursor.rowcount == 1


def update_generated_job(
    job_id: str,
    description: str,
    cover_letter: str,
    analysis: dict,
    warnings: list[str],
    strengths: list[str],
    letter_version: int,
) -> bool:
    """Обновляет только письмо, которое пользователь не редактировал вручную."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE vacancies
            SET description = ?, cover_letter = ?, analysis_json = ?,
                warnings_json = ?, strengths_json = ?, letter_version = ?,
                letter_edited = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ? AND letter_edited = 0
            """,
            (
                description,
                cover_letter,
                json.dumps(analysis, ensure_ascii=False),
                json.dumps(warnings, ensure_ascii=False),
                json.dumps(strengths, ensure_ascii=False),
                letter_version,
                job_id,
                PENDING,
            ),
        )
    return cursor.rowcount == 1


def add_job_warning(job_id: str, warning: str) -> bool:
    job = get_job(job_id)
    if not job or job["status"] != PENDING:
        return False
    try:
        warnings = json.loads(job["warnings_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        warnings = []
    if warning not in warnings:
        warnings.append(warning)
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE vacancies
            SET warnings_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (json.dumps(warnings, ensure_ascii=False), job_id, PENDING),
        )
    return cursor.rowcount == 1


def skip_pending_job(job_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE vacancies
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (SKIPPED, job_id, PENDING),
        )
    return cursor.rowcount == 1


def claim_pending_job(job_id: str) -> Optional[dict]:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE vacancies
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (APPLYING, job_id, PENDING),
        )
        if cursor.rowcount != 1:
            return None
        row = conn.execute(
            """
            SELECT {VACANCY_FIELDS}
            FROM vacancies
            WHERE id = ?
            """.format(VACANCY_FIELDS=VACANCY_FIELDS),
            (job_id,),
        ).fetchone()
    return dict(row)


def restore_pending_job(job_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE vacancies
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (PENDING, job_id, APPLYING),
        )
    return cursor.rowcount == 1


def mark_job_applied(job_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE vacancies
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (APPLIED, job_id, APPLYING),
        )
    return cursor.rowcount == 1


def add_applied_job(job_id: str, title: str, url: str) -> None:
    """Совместимость со старым кодом и существующими базами."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO applied_jobs (id, title, url)
            VALUES (?, ?, ?)
            """,
            (job_id, title, url),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO vacancies (id, title, url, status)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, title, url, APPLIED),
        )


def is_message_processed(msg_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM chat_messages WHERE msg_id = ?", (msg_id,)
        ).fetchone()
    return row is not None


def add_processed_message(msg_id: str, chat_id: str, text: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO chat_messages (msg_id, chat_id, text)
            VALUES (?, ?, ?)
            """,
            (msg_id, chat_id, text),
        )


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
