"""SQLite database schema and helpers for Timelinema."""

import sqlite3
from pathlib import Path

SCHEMA = """
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


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
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
) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (filename, title, start_timestamp, width, height) "
        "VALUES (?, ?, ?, ?, ?)",
        (filename, title, start_timestamp, width, height),
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

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    count = conn.execute(
        f"SELECT COUNT(*) FROM commands c {where}", params
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


def get_sessions(conn: sqlite3.Connection) -> list[dict]:
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
