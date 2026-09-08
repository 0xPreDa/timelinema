"""Asciinema file parser.

Extracts commands with absolute timestamps using header timestamp + event offset.
Reads asciinema v2/v3 recordings.
Uses prompt_detector for auto-detecting shell prompt formats.
"""

import gzip
import json
import math
import warnings
import re
from pathlib import Path

from ansi2html import Ansi2HTMLConverter

from .prompt_detector import detect_strategy
from .terminal import shell_events, strip_escapes

# Other control chars (keep \n, \r, \t)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a]")

# SGR (color/style) sequences only: \x1b[...m
SGR_RE = re.compile(r"\x1b\[[\d;]*m")

_converter = Ansi2HTMLConverter(inline=True, dark_bg=True, scheme="xterm")


def strip_ansi_sgr(text: str) -> str:
    """Remove ANSI SGR (color/style) escape sequences from text."""
    return SGR_RE.sub("", text)


def strip_control_sequences(text: str) -> str:
    """Keep SGR (color) sequences, remove everything else."""
    text = strip_escapes(text, keep_sgr=True)
    text = CTRL_RE.sub("", text)

    # Handle carriage returns (progress bars, spinners)
    lines = text.split("\n")
    processed = []
    for line in lines:
        if "\r" in line:
            parts = line.split("\r")
            # Take the last non-empty segment (simulates terminal overwrite)
            result = ""
            for part in parts:
                if part:
                    result = part
            processed.append(result)
        else:
            processed.append(line)
    return "\n".join(processed)


def render_output_html(raw_output: str) -> str:
    """Convert raw terminal output with ANSI codes to styled HTML."""
    cleaned = strip_control_sequences(raw_output)
    # Remove leading/trailing blank lines
    cleaned = cleaned.strip("\n")
    if not cleaned.strip():
        return ""
    html = _converter.convert(cleaned, full=False)
    return html


def is_valid_asciinema(data: bytes) -> tuple[bool, bool]:
    """Check if data is a valid asciinema v2/v3 recording.

    Returns (is_valid, is_gzipped).
    """
    is_gz = data[:2] == b"\x1f\x8b"
    try:
        if is_gz:
            text = gzip.decompress(data).decode("utf-8", errors="replace")
        else:
            text = data.decode("utf-8", errors="replace")
        first_line = text.split("\n", 1)[0].strip()
        header = json.loads(first_line)
        return isinstance(header, dict) and header.get("version") in (2, 3), is_gz
    except Exception:
        return False, False


def load_asciinema(filepath: str | Path):
    """Load an asciinema v2/v3 file (plain or gzipped).

    Returns (header_dict, events_list).
    """
    filepath = Path(filepath)
    with filepath.open("rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    open_fn = gzip.open if compressed else open

    with open_fn(filepath, "rt", encoding="utf-8-sig", errors="replace") as f:
        header = json.loads(f.readline())
        if not isinstance(header, dict) or header.get("version") not in (2, 3):
            raise ValueError("Only asciinema v2/v3 and v3 recordings are supported")
        version = header["version"]
        if version == 3:
            term = header.get("term", {})
            header = {**header, "width": term.get("cols"), "height": term.get("rows")}
        events = []
        elapsed = 0.0
        for number, line in enumerate(f, 2):
            line = line.strip()
            if not line or (version == 3 and line.startswith("#")):
                continue
            try:
                event = json.loads(line)
                if (not isinstance(event, list) or len(event) != 3
                        or isinstance(event[0], bool)
                        or not isinstance(event[0], (int, float))
                        or not math.isfinite(event[0]) or event[0] < 0
                        or not isinstance(event[1], str) or not isinstance(event[2], str)):
                    raise ValueError("invalid event")
                elapsed = elapsed + event[0] if version == 3 else event[0]
                events.append([elapsed, event[1], event[2]])
            except (ValueError, TypeError):
                # Losing one delta in v3 invalidates every subsequent timestamp.
                if version == 3:
                    raise ValueError(f"Invalid asciinema v3 event at line {number}") from None
                warnings.warn(f"Skipping invalid asciinema event at line {number}", stacklevel=2)

    return header, events


def parse_session(filepath: str | Path) -> tuple[dict, list[dict]]:
    """Parse an asciinema file and extract commands with absolute timestamps.

    Uses header timestamp + event offset for absolute timing.

    Returns (session_info, commands_list) where each command is a dict with:
        absolute_timestamp, command, output_raw, output_html,
        working_directory, duration
    """
    header, events = load_asciinema(filepath)

    base_ts = header.get("timestamp", 0)

    session_info = {
        "filename": Path(filepath).name,
        "title": header.get("title", ""),
        "start_timestamp": base_ts,
        "width": header.get("width"),
        "height": header.get("height"),
    }

    # Auto-detect prompt format and extract command boundaries
    events = list(shell_events(events))
    strategy = detect_strategy(events, header.get("width") or 80, header.get("height") or 24)
    boundaries = strategy.detect(events)

    # Convert boundaries to command dicts
    commands = []
    for b in boundaries:
        raw_output = "".join(
            ev[2] for ev in events[b.output_start_index:b.output_end_index]
            if ev[1] == "o"
        )
        commands.append({
            "absolute_timestamp": base_ts + b.command_offset,
            "command": b.command,
            "output_raw": raw_output,
            "output_html": render_output_html(raw_output),
            "working_directory": b.working_directory or "",
            "duration": None,
        })

    # Compute durations
    for i in range(len(commands) - 1):
        commands[i]["duration"] = round(
            commands[i + 1]["absolute_timestamp"] - commands[i]["absolute_timestamp"],
            1,
        )

    # Last command: compute duration from the last event in the recording
    if commands and events:
        last_event_ts = base_ts + events[-1][0]
        commands[-1]["duration"] = round(
            last_event_ts - commands[-1]["absolute_timestamp"],
            1,
        )

    # Fill empty working directories with the last known CWD
    last_cwd = ""
    for cmd in commands:
        if cmd["working_directory"]:
            last_cwd = cmd["working_directory"]
        elif last_cwd:
            cmd["working_directory"] = last_cwd

    return session_info, commands


def parse_data_directory(data_dir: str | Path) -> list[tuple[dict, list[dict]]]:
    """Parse all asciinema files in a directory.

    Returns list of (session_info, commands) tuples.
    """
    data_dir = Path(data_dir)
    results = []

    patterns = ["*.asciinema", "*.asciinema.gz", "*.cast", "*.cast.gz"]
    # Keep legacy/manual recordings readable, and discover browser uploads.
    # Prefer the uploaded copy when a legacy file has the same name.
    by_name = {}
    for directory in (data_dir, data_dir / "uploads"):
        for pattern in patterns:
            for filepath in directory.glob(pattern):
                by_name[filepath.name] = filepath
    files = list(by_name.values())

    for filepath in sorted(files):
        try:
            session_info, commands = parse_session(filepath)
            if commands:
                results.append((session_info, commands))
        except Exception as e:
            print(f"Warning: failed to parse {filepath}: {e}")

    return results
