# Timelinema

Timeline viewer for [asciinema](https://asciinema.org/) terminal recordings. Parses asciinema v2 and v3 files, extracts individual commands with their output, and presents them in a searchable, chronological web interface.

## Features

- **Command extraction** - Reconstructs shell commands from terminal echo, shell integration (OSC 133), and terminal titles
- **Output preservation** - Renders ANSI color codes as styled HTML
- **Search** - Multi-keyword search across all commands with highlight, navigation, and a clear button
- **Projects** - Organize sessions into projects via a sidebar, create/rename/delete projects
- **Authentication** - Optional shared-password protection via TOML config file
- **Import/Export** - Export a project as a ZIP archive, import it on another instance
- **Session filtering** - View commands from specific recording sessions
- **Dark/Light theme** - Toggle with preference saved in browser
- **Timezone selector** - Display timestamps in any UTC offset
- **Drag-and-drop upload** - Drop `asciinema` files onto the page to add recordings
- **Lazy loading** - Command output fetched on demand for fast initial load

## Requirements

- Python 3.11+

## Installation (Recommended)

```bash
pipx install 'git+https://github.com/0xPreDa/timelinema.git'
```

## Usage

### Foreground

```bash
timelinema [--host HOST] [--port PORT] [--data-dir DIR] [--db DBPATH] [--config CONFIG]
```

### Background (start / stop)

```bash
timelinema-start [--host HOST] [--port PORT] [--data-dir DIR] [--db DBPATH] [--config CONFIG]
timelinema-stop
```

`timelinema-start` launches the server in the background and prints the URL to access it. `timelinema-stop` shuts it down.

### Options

| Option       | Default          | Description                              |
|--------------|------------------|------------------------------------------|
| `--host`     | `127.0.0.1`     | Bind address (`0.0.0.0` for network)     |
| `--port`     | `5000`           | Port number                              |
| `--data-dir` | `.` (current dir)| Directory containing `asciinema` files  |
| `--db`       | `timelinema.db`  | SQLite database path                     |
| `--config`   | *(none)*         | Path to TOML configuration file          |

Example:

```bash
timelinema-start --data-dir ./recordings --port 8080
```

Then open `http://127.0.0.1:8080` in your browser.

## Configuration

All settings can be defined in a TOML file (see [`config.example.toml`](config.example.toml)). CLI arguments take precedence over config values.

```toml
[server]
host = "0.0.0.0"
port = 5000
data_dir = "./data"
db = "timelinema.db"

[auth]
password = "shared-secret"

# Optional: persist sessions across restarts
# secret_key = "a-long-random-string"
```

Launch with:

```bash
timelinema --config config.toml
```

### Authentication

When an `[auth]` section is present in the config, all pages and API endpoints require login. Without it, authentication is disabled (backward-compatible).

Sessions last 30 days and are invalidated server-side on logout.

## Projects

Sessions are organized into projects. A default project is created automatically on first run. Use the sidebar to:

- Create, rename, or delete projects
- Switch between projects (timeline and session filter update accordingly)
- Upload and reload target the active project
- Browser uploads are stored in `uploads/` inside the configured data directory.
  This folder and recording file extensions are ignored by Git. Reload and startup
  scan both this folder and the data directory for legacy/manual recordings;
  an uploaded copy takes precedence over a root-level file with the same name.

Deleting a project removes its session data from the database but does **not** delete `asciinema` files from disk.

### Clear the database

Use **Clear database…** below the project list to remove imported data from all
projects. The dialog shows database-wide totals and requires typing `DELETE`
before the final confirmation. Commands, sessions and projects are removed in
one transaction; one empty Default project remains. Recording files on disk are
kept, so **Reload** or restarting the application can import them again.

## Import / Export

- **Export** - Download the active project as a ZIP archive containing a `project.json` manifest and one JSON file per session. Option to strip ANSI color codes from command output.
- **Import** - Upload a ZIP archive to create a new project with all its sessions. If a project with the same name already exists, a suffix is added. Duplicate sessions (by filename) are skipped.

## Input format

Timelinema accepts asciinema v2 and v3 recordings (`.asciinema`, `.cast`, and their `.gz` variants). Compression is detected from file contents. Place them in your data directory and start the server - files are parsed and indexed into SQLite on first run.

Use the **Reload** button or `POST /api/reload` to import new files and re-parse existing sessions in the selected project. Re-parsing replaces extracted commands atomically while retaining the session and its project. Source recordings must still be present in the data directory.

The parser replays terminal editing with `pyte`, including cursor movement, deletion, completion/history redraws, wrapped lines, and private control sequences split across recording events. It uses the final shell echo rather than search-menu entries or raw keystrokes. Asciinema v3 event intervals are accumulated according to the [v3 format specification](https://docs.asciinema.org/manual/asciicast/v3/); v2 event offsets remain unchanged.

Recordings without reliable shell markers use a conservative input-only fallback. History/completion cannot be recovered from keystrokes alone, so unresolved input is omitted. Missing echo or an incomplete recording can still limit command reconstruction. No recorded command is executed during parsing.

After updating the application, reinstall its dependencies (including `pyte`), restart it, and use **Reload** to apply parser changes to existing sessions.

### Parser tests

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

The regression suite uses synthetic recordings and temporary databases. It checks multiline submissions with the cursor on every edited row, multiple terminal widths, and fragmented events. Optional tests use a private corpus and `regression-manifest.json` stored in the ignored `uploads/` directory (or `TIMELINEMA_RECORDINGS_DIR`). The manifest holds filenames and ordered SHA-256 command fingerprints from a reviewed baseline; the public test code contains no client-derived fixtures. These tests are skipped when the manifest is absent. Keep the manifest private and only update it after reviewing a parsing change.

## API

| Endpoint                              | Method | Description                           |
|---------------------------------------|--------|---------------------------------------|
| `/api/projects`                       | GET    | List projects with session counts     |
| `/api/projects`                       | POST   | Create a project `{name}`             |
| `/api/projects/<id>`                  | PUT    | Rename a project `{name}`             |
| `/api/projects/<id>`                  | DELETE | Delete a project (DB only)            |
| `/api/projects/<id>/export`           | GET    | Download project as ZIP               |
| `/api/projects/import`                | POST   | Import project from ZIP               |
| `/api/sessions`                       | GET    | List sessions (optional `?project_id`)   |
| `/api/timeline`                       | GET    | Paginated commands (search/filter)    |
| `/api/command/<id>`                   | GET    | Full output for a single command      |
| `/api/reload`                         | POST   | Re-parse data directory               |
| `/api/database`                       | GET    | Counts across all projects            |
| `/api/database`                       | DELETE | Clear imported data; JSON `{"confirmation":"DELETE"}` required |
| `/api/upload`                         | POST   | Upload recording files                |
| `/api/auth/login`                     | POST   | Authenticate `{password}`             |
| `/api/auth/logout`                    | POST   | Logout (invalidates session)          |

## Project structure

```
src/timelinema/
  app.py          Flask routes, auth, CLI entry point
  parser.py       Asciinema v2 parser and command extraction
  database.py     SQLite schema, migrations, and queries
  templates/      HTML templates (index, login)
  static/         CSS and JavaScript
```

## License

See [LICENSE](LICENSE).
