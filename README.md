# Timelinema

Timeline viewer for [asciinema](https://asciinema.org/) terminal recordings. Parses asciinema v2 files, extracts individual commands with their output, and presents them in a searchable, chronological web interface.

## Features

- **Command extraction** - Automatically detects commands from terminal title escape sequences (OSC 2/7)
- **Output preservation** - Renders ANSI color codes as styled HTML
- **Search** - Multi-keyword search across all commands with highlight and navigation
- **Projects** - Organize sessions into projects via a sidebar, create/rename/delete projects
- **Authentication** - Optional shared-password protection via TOML config file
- **Import/Export** - Export a project as a ZIP archive, import it on another instance
- **Session filtering** - View commands from specific recording sessions
- **Dark/Light theme** - Toggle with preference saved in browser
- **Timezone selector** - Display timestamps in any UTC offset
- **Drag-and-drop upload** - Drop `.asciinema` files onto the page to add recordings
- **Lazy loading** - Command output fetched on demand for fast initial load

## Requirements

- Python 3.11+

## Installation (Recommended)

```bash
pipx install timelinema
```

## Usage

```bash
timelinema [--host HOST] [--port PORT] [--data-dir DIR] [--db DBPATH] [--config CONFIG]
```

| Option       | Default          | Description                              |
|--------------|------------------|------------------------------------------|
| `--host`     | `127.0.0.1`     | Bind address (`0.0.0.0` for network)     |
| `--port`     | `5000`           | Port number                              |
| `--data-dir` | `./data`         | Directory containing `.asciinema` files  |
| `--db`       | `timelinema.db`  | SQLite database path                     |
| `--config`   | *(none)*         | Path to TOML configuration file          |

Example:

```bash
timelinema --data-dir ./data --port 8080
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

Deleting a project removes its session data from the database but does **not** delete `.asciinema` files from disk.

## Import / Export

- **Export** - Download the active project as a ZIP archive containing a `project.json` manifest and one JSON file per session. Option to strip ANSI color codes from command output.
- **Import** - Upload a ZIP archive to create a new project with all its sessions. If a project with the same name already exists, a suffix is added. Duplicate sessions (by filename) are skipped.

## Input format

Timelinema accepts asciinema v2 recordings, both plain (`.asciinema`) and gzip-compressed (`.asciinema.gz`). Place them in your data directory and start the server - files are parsed and indexed into SQLite on first run.

Use the **Reload** button or `POST /api/reload` to re-parse after adding new files.

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

See [LICENSE](LICENSE) if present.
