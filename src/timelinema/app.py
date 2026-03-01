"""Timelinema - Timeline viewer for asciinema recordings."""

import argparse
import io
import json
import secrets
import time
import tomllib
import zipfile
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from . import database, parser

ALLOWED_EXTENSIONS = {".asciinema", ".asciinema.gz"}


def create_app(
    db_path: str = "timelinema.db",
    data_dir: str = "./data",
    config: dict | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    app.config["DATA_DIR"] = data_dir

    # Auth configuration
    auth_password = None
    if config and "auth" in config:
        auth_password = config["auth"].get("password")

    app.config["AUTH_ENABLED"] = auth_password is not None
    app.config["AUTH_PASSWORD"] = auth_password

    # Secret key for sessions
    if config and config.get("secret_key"):
        app.secret_key = config["secret_key"]
    else:
        app.secret_key = secrets.token_hex(32)

    from datetime import timedelta

    app.permanent_session_lifetime = timedelta(days=30)

    # Server-side session token store (invalidated on logout)
    active_session_tokens: set[str] = set()

    # --- Auth middleware ---
    @app.before_request
    def check_auth():
        if not app.config["AUTH_ENABLED"]:
            return None

        # Allow login routes and static files
        if request.path in ("/login", "/api/auth/login") or request.path.startswith(
            "/static/"
        ):
            return None

        token = session.get("token")
        if not session.get("authenticated") or not token or token not in active_session_tokens:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login_page"))
        return None

    # --- Auth routes ---
    @app.route("/login")
    def login_page():
        if not app.config["AUTH_ENABLED"] or session.get("authenticated"):
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.route("/api/auth/login", methods=["POST"])
    def api_login():
        if not app.config["AUTH_ENABLED"]:
            return jsonify({"error": "Auth not enabled"}), 400

        data = request.get_json(silent=True) or {}
        password = data.get("password", "")

        if password == app.config["AUTH_PASSWORD"]:
            session.permanent = True
            session["authenticated"] = True
            token = secrets.token_hex(16)
            session["token"] = token
            active_session_tokens.add(token)
            return jsonify({"status": "ok"})
        return jsonify({"error": "Invalid password"}), 403

    @app.route("/api/auth/logout", methods=["POST"])
    def api_logout():
        token = session.get("token")
        if token:
            active_session_tokens.discard(token)
        session.clear()
        return jsonify({"status": "ok"})

    # --- Pages ---
    @app.route("/")
    def index():
        return render_template(
            "index.html", auth_enabled=app.config["AUTH_ENABLED"]
        )

    # --- Project API ---
    @app.route("/api/projects")
    def api_projects():
        conn = database.get_connection(app.config["DB_PATH"])
        try:
            projects = database.get_projects(conn)
            return jsonify(projects)
        finally:
            conn.close()

    @app.route("/api/projects", methods=["POST"])
    def api_create_project():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Name required"}), 400

        conn = database.get_connection(app.config["DB_PATH"])
        try:
            project_id = database.create_project(conn, name)
            return jsonify({"id": project_id, "name": name})
        finally:
            conn.close()

    @app.route("/api/projects/<int:project_id>", methods=["DELETE"])
    def api_delete_project(project_id):
        conn = database.get_connection(app.config["DB_PATH"])
        try:
            database.delete_project(conn, project_id)
            return jsonify({"status": "ok"})
        finally:
            conn.close()

    @app.route("/api/projects/<int:project_id>", methods=["PUT"])
    def api_rename_project(project_id):
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Name required"}), 400

        conn = database.get_connection(app.config["DB_PATH"])
        try:
            database.rename_project(conn, project_id, name)
            return jsonify({"status": "ok", "name": name})
        finally:
            conn.close()

    # --- Session / Timeline API ---
    @app.route("/api/sessions")
    def api_sessions():
        project_id = request.args.get("project_id", type=int)
        conn = database.get_connection(app.config["DB_PATH"])
        try:
            sessions = database.get_sessions(conn, project_id=project_id)
            return jsonify(sessions)
        finally:
            conn.close()

    @app.route("/api/timeline")
    def api_timeline():
        search = request.args.get("search", "").strip() or None
        session_id = request.args.get("session_id", type=int)
        project_id = request.args.get("project_id", type=int)
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 500, type=int)

        conn = database.get_connection(app.config["DB_PATH"])
        try:
            commands, total = database.get_timeline(
                conn,
                search=search,
                session_id=session_id,
                project_id=project_id,
                page=page,
                per_page=per_page,
            )
            return jsonify(
                {
                    "commands": commands,
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                }
            )
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
        data = request.get_json(silent=True) or {}
        project_id = data.get("project_id")
        count = load_data(
            app.config["DATA_DIR"], app.config["DB_PATH"], project_id=project_id
        )
        return jsonify({"status": "ok", "sessions_loaded": count})

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "No files provided"}), 400

        project_id = request.form.get("project_id", type=int)

        data_dir = Path(app.config["DATA_DIR"])
        data_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        skipped = []
        for f in files:
            name = f.filename or ""
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

        sessions_loaded = 0
        if saved:
            sessions_loaded = load_data(
                str(data_dir), app.config["DB_PATH"], project_id=project_id
            )

        return jsonify(
            {
                "status": "ok",
                "saved": saved,
                "skipped": skipped,
                "sessions_loaded": sessions_loaded,
            }
        )

    # --- Export / Import ---
    @app.route("/api/projects/<int:project_id>/export")
    def api_export_project(project_id):
        include_ansi = request.args.get("include_ansi", "1") == "1"

        conn = database.get_connection(app.config["DB_PATH"])
        try:
            data = database.get_project_export_data(conn, project_id)
            if not data:
                return jsonify({"error": "Project not found"}), 404

            project_name = data["project"]["name"]

            # Build project.json
            export_data = {
                "timelinema_version": "1.0.0",
                "export_version": 1,
                "exported_at": time.time(),
                "include_ansi": include_ansi,
                "project": {"name": project_name},
                "sessions": [],
            }

            session_files = []

            for sess in data["sessions"]:
                commands_export = []
                for cmd in sess["commands"]:
                    raw = cmd.get("output_raw", "") or ""
                    if not include_ansi:
                        raw = parser.strip_ansi_sgr(raw)
                    commands_export.append(
                        {
                            "absolute_timestamp": cmd["absolute_timestamp"],
                            "command": cmd["command"],
                            "output_raw": raw,
                            "working_directory": cmd.get("working_directory"),
                            "duration": cmd.get("duration"),
                        }
                    )

                session_entry = {
                    "filename": sess["filename"],
                    "title": sess.get("title"),
                    "start_timestamp": sess.get("start_timestamp"),
                    "width": sess.get("width"),
                    "height": sess.get("height"),
                    "commands": commands_export,
                }

                export_data["sessions"].append(session_entry)

                # Individual session file
                session_files.append(
                    (
                        f"session_{sess['filename']}.json",
                        session_entry,
                    )
                )

            # Create ZIP in memory
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "project.json",
                    json.dumps(export_data, ensure_ascii=False, indent=2),
                )
                for fname, sdata in session_files:
                    zf.writestr(
                        fname,
                        json.dumps(sdata, ensure_ascii=False, indent=2),
                    )

            buf.seek(0)

            from flask import send_file

            safe_project_name = "".join(
                c if c.isalnum() or c in " _-" else "_" for c in project_name
            ).strip()

            return send_file(
                buf,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{safe_project_name}.zip",
            )
        finally:
            conn.close()

    @app.route("/api/projects/import", methods=["POST"])
    def api_import_project():
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]
        if not f.filename or not f.filename.endswith(".zip"):
            return jsonify({"error": "ZIP file required"}), 400

        try:
            zip_bytes = f.read()
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                if "project.json" not in zf.namelist():
                    return jsonify({"error": "Invalid archive: missing project.json"}), 400

                project_data = json.loads(zf.read("project.json"))

            project_name = project_data.get("project", {}).get("name", "Imported")
            sessions_data = project_data.get("sessions", [])

            if not sessions_data:
                return jsonify({"error": "No sessions in archive"}), 400

            conn = database.get_connection(app.config["DB_PATH"])
            try:
                result = database.import_project_data(
                    conn,
                    project_name,
                    sessions_data,
                    parser.render_output_html,
                )
                return jsonify(result)
            finally:
                conn.close()

        except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as e:
            return jsonify({"error": f"Invalid archive: {e}"}), 400

    return app


def load_data(
    data_dir: str, db_path: str, project_id: int | None = None
) -> int:
    """Parse asciinema files and store in database. Returns number of new sessions."""
    conn = database.init_db(db_path)
    try:
        # If no project_id provided, use Default project
        if project_id is None:
            row = conn.execute(
                "SELECT id FROM projects WHERE name = 'Default'"
            ).fetchone()
            if row:
                project_id = row[0]

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
                project_id=project_id,
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
        default=None,
        help="Bind address (default: 127.0.0.1)",
    )
    ap.add_argument("--port", type=int, default=None, help="Port (default: 5000)")
    ap.add_argument(
        "--data-dir", default=None, help="Directory with .asciinema files"
    )
    ap.add_argument("--db", default=None, help="SQLite database path")
    ap.add_argument("--config", default=None, help="Path to TOML configuration file")
    args = ap.parse_args()

    # Load config
    config = None
    if args.config:
        config_path = Path(args.config)
        if not config_path.is_file():
            print(f"Error: config file not found: {config_path}")
            return
        with open(config_path, "rb") as f:
            config = tomllib.load(f)

    # Resolve settings: CLI args override config, which overrides defaults
    server_cfg = (config or {}).get("server", {})
    host = args.host or server_cfg.get("host", "127.0.0.1")
    port = args.port or server_cfg.get("port", 5000)
    data_dir_str = args.data_dir or server_cfg.get("data_dir", "./data")
    db_path = args.db or server_cfg.get("db", "timelinema.db")

    data_dir = Path(data_dir_str)
    if not data_dir.is_dir():
        print(f"Error: data directory not found: {data_dir}")
        return

    print(f"Parsing asciinema files from {data_dir}...")
    count = load_data(str(data_dir), db_path)
    print(f"Loaded {count} new session(s).")

    app = create_app(db_path=db_path, data_dir=str(data_dir), config=config)
    print(f"\nTimelinema running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
