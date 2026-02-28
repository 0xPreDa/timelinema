"""Timelinema - Timeline viewer for asciinema recordings."""

import argparse
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from . import database, parser

ALLOWED_EXTENSIONS = {".asciinema", ".asciinema.gz"}


def create_app(db_path: str = "timelinema.db", data_dir: str = "./data") -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    app.config["DATA_DIR"] = data_dir

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/sessions")
    def api_sessions():
        conn = database.get_connection(app.config["DB_PATH"])
        try:
            sessions = database.get_sessions(conn)
            return jsonify(sessions)
        finally:
            conn.close()

    @app.route("/api/timeline")
    def api_timeline():
        search = request.args.get("search", "").strip() or None
        session_id = request.args.get("session_id", type=int)
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 500, type=int)

        conn = database.get_connection(app.config["DB_PATH"])
        try:
            commands, total = database.get_timeline(
                conn,
                search=search,
                session_id=session_id,
                page=page,
                per_page=per_page,
            )
            # Send raw epoch; frontend formats in user's timezone

            return jsonify({
                "commands": commands,
                "total": total,
                "page": page,
                "per_page": per_page,
            })
        finally:
            conn.close()

    @app.route("/api/command/<int:command_id>")
    def api_command_detail(command_id):
        conn = database.get_connection(app.config["DB_PATH"])
        try:
            cmd = database.get_command_detail(conn, command_id)
            if cmd is None:
                return jsonify({"error": "Not found"}), 404
            return jsonify(cmd)
        finally:
            conn.close()

    @app.route("/api/reload", methods=["POST"])
    def api_reload():
        count = load_data(app.config["DATA_DIR"], app.config["DB_PATH"])
        return jsonify({"status": "ok", "sessions_loaded": count})

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "No files provided"}), 400

        data_dir = Path(app.config["DATA_DIR"])
        data_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        skipped = []
        for f in files:
            name = f.filename or ""
            # Validate extension (.asciinema or .asciinema.gz)
            if name.endswith(".asciinema.gz"):
                ext = ".asciinema.gz"
            elif name.endswith(".asciinema"):
                ext = ".asciinema"
            else:
                skipped.append(name)
                continue

            safe_name = secure_filename(name)
            if not safe_name:
                skipped.append(name)
                continue

            dest = data_dir / safe_name
            f.save(str(dest))
            saved.append(safe_name)

        # Parse newly uploaded files
        sessions_loaded = 0
        if saved:
            sessions_loaded = load_data(str(data_dir), app.config["DB_PATH"])

        return jsonify({
            "status": "ok",
            "saved": saved,
            "skipped": skipped,
            "sessions_loaded": sessions_loaded,
        })

    return app


def load_data(data_dir: str, db_path: str) -> int:
    """Parse asciinema files and store in database. Returns number of new sessions."""
    conn = database.init_db(db_path)
    try:
        results = parser.parse_data_directory(data_dir)
        count = 0
        for session_info, commands in results:
            if database.session_exists(conn, session_info["filename"]):
                continue

            session_id = database.insert_session(
                conn,
                filename=session_info["filename"],
                title=session_info["title"],
                start_timestamp=session_info["start_timestamp"],
                width=session_info["width"],
                height=session_info["height"],
            )

            for cmd in commands:
                cmd["session_id"] = session_id

            database.insert_commands(conn, commands)
            count += 1
            print(f"  Loaded: {session_info['filename']} ({len(commands)} commands)")

        return count
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(
        description="Timelinema - Timeline viewer for asciinema recordings"
    )
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (use 0.0.0.0 for network access, default: 127.0.0.1)",
    )
    ap.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    ap.add_argument(
        "--data-dir", default="./data", help="Directory with .asciinema files"
    )
    ap.add_argument("--db", default="timelinema.db", help="SQLite database path")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"Error: data directory not found: {data_dir}")
        return

    print(f"Parsing asciinema files from {data_dir}...")
    count = load_data(str(data_dir), args.db)
    print(f"Loaded {count} new session(s).")

    app = create_app(db_path=args.db, data_dir=str(data_dir))
    print(f"\nTimelinema running at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
