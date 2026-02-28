# Timelinema

Timeline viewer for [asciinema](https://asciinema.org/) terminal recordings. Parses asciinema v2 files, extracts individual commands with their output, and presents them in a searchable, chronological web interface.

## Features

- **Command extraction** - Automatically detects commands from terminal title escape sequences (OSC 2/7)
- **Output preservation** - Renders ANSI color codes as styled HTML
- **Search** - Multi-keyword search across all commands with highlight and navigation
- **Session filtering** - View commands from specific recording sessions
- **Dark/Light theme** - Toggle with preference saved in browser
- **Timezone selector** - Display timestamps in any UTC offset
- **Drag-and-drop upload** - Drop `.asciinema` files onto the page to add recordings
- **Lazy loading** - Command output fetched on demand for fast initial load

## Requirements

- Python 3.11+

## Installation (Reccomended)

```bash
pipx install timelinema
```

## Usage

```bash
timelinema [--host HOST] [--port PORT] [--data-dir DIR] [--db DBPATH]
```

| Option       | Default          | Description                              |
|--------------|------------------|------------------------------------------|
| `--host`     | `127.0.0.1`     | Bind address (`0.0.0.0` for network)     |
| `--port`     | `5000`           | Port number                              |
| `--data-dir` | `./data`         | Directory containing `.asciinema` files  |
| `--db`       | `timelinema.db`  | SQLite database path                     |

Example:

```bash
timelinema --data-dir ./data --port 8080
```

Then open `http://127.0.0.1:8080` in your browser.

## Input format

Timelinema accepts asciinema v2 recordings, both plain (`.asciinema`) and gzip-compressed (`.asciinema.gz`). Place them in your data directory and start the server - files are parsed and indexed into SQLite on first run.

Use the **Reload** button or `POST /api/reload` to re-parse after adding new files.

## API

| Endpoint               | Method | Description                          |
|------------------------|--------|--------------------------------------|
| `/api/sessions`        | GET    | List sessions with command counts    |
| `/api/timeline`        | GET    | Paginated commands (search/filter)   |
| `/api/command/<id>`    | GET    | Full output for a single command     |
| `/api/reload`          | POST   | Re-parse data directory              |
| `/api/upload`          | POST   | Upload a new recording file          |

## Project structure

```
src/timelinema/
  app.py          Flask routes and CLI entry point
  parser.py       Asciinema v2 parser and command extraction
  database.py     SQLite schema and queries
  templates/      HTML template
  static/         CSS and JavaScript
```

## License

See [LICENSE](LICENSE) if present.
