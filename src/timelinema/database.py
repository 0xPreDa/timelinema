"""SQLite database schema and helpers for Timelinema."""

import sqlite3
import time
from pathlib import Path

CURRENT_SCHEMA_VERSION = 2

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    title TEXT,
    start_timestamp REAL,
    width INTEGER,
    height INTEGER
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    absolute_timestamp REAL NOT NULL,
    command TEXT NOT NULL,
    output_raw TEXT,
    output_html TEXT,
    working_directory TEXT,
    duration REAL
);

CREATE INDEX IF NOT EXISTS idx_commands_timestamp ON commands(absolute_timestamp);
CREATE INDEX IF NOT EXISTS idx_commands_command ON commands(command);
"""

MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version, 0 if no version table exists."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row[0] if row else 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def init_db(db_path: str | Path) -> sqlite3.Connection:
    conn = get_connection(db_path)
    version = _get_schema_version(conn)

    if version < 1:
        conn.executescript(SCHEMA_V1)
        _set_schema_version(conn, 1)
        conn.commit()
        version = 1

    if version < 2:
        conn.executescript(MIGRATION_V2)
        # Add project_id column to sessions if it doesn't exist
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "project_id" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN project_id INTEGER REFERENCES projects(id)")
        # Create default project and assign all existing sessions
        row = conn.execute("SELECT id FROM projects WHERE name = 'Default'").fetchone()
        if row:
            default_id = row[0]
        else:
            cur = conn.execute(
                "INSERT INTO projects (name, created_at) VALUES (?, ?)",
                ("Default", time.time()),
            )
            default_id = cur.lastrowid
        conn.execute(
            "UPDATE sessions SET project_id = ? WHERE project_id IS NULL",
            (default_id,),
        )
        _set_schema_version(conn, 2)
        conn.commit()

    return conn


def session_exists(conn: sqlite3.Connection, filename: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sessions WHERE filename = ?", (filename,)
    ).fetchone()
    return row is not None


def insert_session(
    conn: sqlite3.Connection,
    filename: str,
    title: str | None,
    start_timestamp: float | None,
    width: int | None,
    height: int | None,
    project_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (filename, title, start_timestamp, width, height, project_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (filename, title, start_timestamp, width, height, project_id),
    )
    conn.commit()
    return cur.lastrowid


def insert_commands(
    conn: sqlite3.Connection,
    commands: list[dict],
) -> None:
    conn.executemany(
        "INSERT INTO commands "
        "(session_id, absolute_timestamp, command, output_raw, output_html, "
        "working_directory, duration) "
        "VALUES (:session_id, :absolute_timestamp, :command, :output_raw, "
        ":output_html, :working_directory, :duration)",
        commands,
    )
    conn.commit()


def get_timeline(
    conn: sqlite3.Connection,
    search: str | None = None,
    session_id: int | None = None,
    project_id: int | None = None,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict], int]:
    conditions = []
    params: list = []

    if search:
        conditions.append("c.command LIKE ?")
        params.append(f"%{search}%")
    if session_id:
        conditions.append("c.session_id = ?")
        params.append(session_id)
    if project_id:
        conditions.append("s.project_id = ?")
        params.append(project_id)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    count = conn.execute(
        f"SELECT COUNT(*) FROM commands c JOIN sessions s ON c.session_id = s.id {where}",
        params,
    ).fetchone()[0]

    offset = (page - 1) * per_page
    rows = conn.execute(
        f"SELECT c.id, c.session_id, s.title as session_title, "
        f"c.absolute_timestamp, c.command, c.working_directory, c.duration, "
        f"(c.output_html IS NOT NULL AND c.output_html != '') as has_output "
        f"FROM commands c JOIN sessions s ON c.session_id = s.id "
        f"{where} "
        f"ORDER BY c.absolute_timestamp ASC "
        f"LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()

    return [dict(r) for r in rows], count


def get_command_detail(conn: sqlite3.Connection, command_id: int) -> dict | None:
    row = conn.execute(
        "SELECT c.*, s.title as session_title "
        "FROM commands c JOIN sessions s ON c.session_id = s.id "
        "WHERE c.id = ?",
        (command_id,),
    ).fetchone()
    return dict(row) if row else None


def get_sessions(
    conn: sqlite3.Connection,
    project_id: int | None = None,
) -> list[dict]:
    if project_id:
        rows = conn.execute(
            "SELECT s.*, COUNT(c.id) as command_count "
            "FROM sessions s LEFT JOIN commands c ON s.id = c.session_id "
            "WHERE s.project_id = ? "
            "GROUP BY s.id ORDER BY s.start_timestamp",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT s.*, COUNT(c.id) as command_count "
            "FROM sessions s LEFT JOIN commands c ON s.id = c.session_id "
            "GROUP BY s.id ORDER BY s.start_timestamp"
        ).fetchall()
    return [dict(r) for r in rows]


def clear_all(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM commands")
    conn.execute("DELETE FROM sessions")
    conn.commit()


# --- Project CRUD ---

def get_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT p.*, "
        "(SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id) as session_count "
        "FROM projects p ORDER BY p.created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def create_project(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO projects (name, created_at) VALUES (?, ?)",
        (name, time.time()),
    )
    conn.commit()
    return cur.lastrowid


def delete_project(conn: sqlite3.Connection, project_id: int) -> None:
    """Delete project and its DB data (sessions + commands). Does NOT delete files on disk."""
    # Delete commands belonging to sessions in this project
    conn.execute(
        "DELETE FROM commands WHERE session_id IN "
        "(SELECT id FROM sessions WHERE project_id = ?)",
        (project_id,),
    )
    conn.execute("DELETE FROM sessions WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()


def rename_project(conn: sqlite3.Connection, project_id: int, name: str) -> None:
    conn.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))
    conn.commit()


# --- Export / Import helpers ---

def get_project_export_data(conn: sqlite3.Connection, project_id: int) -> dict | None:
    """Get full project data for export: project info + sessions with commands."""
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not project:
        return None

    sessions = conn.execute(
        "SELECT * FROM sessions WHERE project_id = ? ORDER BY start_timestamp",
        (project_id,),
    ).fetchall()

    result = {
        "project": dict(project),
        "sessions": [],
    }

    for session in sessions:
        session_dict = dict(session)
        commands = conn.execute(
            "SELECT absolute_timestamp, command, output_raw, working_directory, duration "
            "FROM commands WHERE session_id = ? ORDER BY absolute_timestamp",
            (session_dict["id"],),
        ).fetchall()
        session_dict["commands"] = [dict(c) for c in commands]
        result["sessions"].append(session_dict)

    return result


def import_project_data(
    conn: sqlite3.Connection,
    project_name: str,
    sessions_data: list[dict],
    render_html_fn,
) -> dict:
    """Import project data. Returns summary dict with counts.

    render_html_fn: callable(raw_output) -> html_output
    """
    # Handle duplicate project names
    existing = conn.execute(
        "SELECT name FROM projects WHERE name = ?", (project_name,)
    ).fetchone()
    if existing:
        # Find next available suffix
        suffix = 2
        while True:
            candidate = f"{project_name} ({suffix})"
            if not conn.execute(
                "SELECT 1 FROM projects WHERE name = ?", (candidate,)
            ).fetchone():
                project_name = candidate
                break
            suffix += 1

    project_id = None
    imported = 0
    skipped = 0

    try:
        cur = conn.execute(
            "INSERT INTO projects (name, created_at) VALUES (?, ?)",
            (project_name, time.time()),
        )
        project_id = cur.lastrowid

        for session_data in sessions_data:
            filename = session_data.get("filename", "")
            if not filename:
                skipped += 1
                continue

            # Skip if filename already exists
            if session_exists(conn, filename):
                skipped += 1
                continue

            session_id_cur = conn.execute(
                "INSERT INTO sessions (filename, title, start_timestamp, width, height, project_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    filename,
                    session_data.get("title"),
                    session_data.get("start_timestamp"),
                    session_data.get("width"),
                    session_data.get("height"),
                    project_id,
                ),
            )
            sid = session_id_cur.lastrowid

            commands = session_data.get("commands", [])
            for cmd in commands:
                raw = cmd.get("output_raw", "")
                html = render_html_fn(raw) if raw else ""
                conn.execute(
                    "INSERT INTO commands "
                    "(session_id, absolute_timestamp, command, output_raw, output_html, "
                    "working_directory, duration) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        sid,
                        cmd.get("absolute_timestamp", 0),
                        cmd.get("command", ""),
                        raw,
                        html,
                        cmd.get("working_directory"),
                        cmd.get("duration"),
                    ),
                )

            imported += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "project_id": project_id,
        "project_name": project_name,
        "imported": imported,
        "skipped": skipped,
    }
