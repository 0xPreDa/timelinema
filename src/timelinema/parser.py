"""Asciinema file parser.

Extracts commands with absolute timestamps using header timestamp + event offset.
Compatible with any asciinema v2 recording.
"""

import gzip
import json
import re
from pathlib import Path

from ansi2html import Ansi2HTMLConverter

# Terminal title set by zsh before running a command: \x1b]2;command\x07
TITLE_RE = re.compile(r"\x1b\]2;(.+?)\x07")

# Terminal title reset to default prompt (not a command)
ROOT_TITLE_RE = re.compile(r"^root@")

# Working directory from OSC 7: \x1b]7;file://host/path\x1b\\
OSC7_CWD_RE = re.compile(r"\x1b\]7;file://[^/]*(/.+?)\x1b\\")

# OSC sequences: \x1b]...\x07 or \x1b]...\x1b\\
OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")

# CSI sequences that are NOT SGR (color): \x1b[...X where X is not 'm'
CSI_NON_SGR_RE = re.compile(r"\x1b\[[\d;]*[A-LN-Za-ln-z]")

# Other control chars (keep \n, \r, \t)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a]")

# SGR (color/style) sequences only: \x1b[...m
SGR_RE = re.compile(r"\x1b\[[\d;]*m")

_converter = Ansi2HTMLConverter(inline=True, dark_bg=True, scheme="xterm")


def strip_ansi_sgr(text: str) -> str:
    """Remove ANSI SGR (color/style) escape sequences from text."""
    return SGR_RE.sub("", text)


def extract_command_from_title(data: str) -> str | None:
    """Extract command from terminal title set event."""
    match = TITLE_RE.search(data)
    if not match:
        return None
    title = match.group(1)
    if ROOT_TITLE_RE.match(title):
        return None
    # Filter out file:// and other non-command titles
    if title.startswith("file://") or title.startswith("/"):
        return None
    return title


def strip_control_sequences(text: str) -> str:
    """Keep SGR (color) sequences, remove everything else."""
    text = OSC_RE.sub("", text)
    text = CSI_NON_SGR_RE.sub("", text)
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


def load_asciinema(filepath: str | Path):
    """Load an asciinema v2 file (plain or gzipped).

    Returns (header_dict, events_list).
    """
    filepath = Path(filepath)
    open_fn = gzip.open if filepath.name.endswith(".gz") else open

    with open_fn(filepath, "rt", encoding="utf-8", errors="replace") as f:
        header = json.loads(f.readline())
        events = []
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
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

    commands = []

    # State machine
    current_command_offset = None
    current_cwd = ""
    current_command = None
    output_parts: list[str] = []
    waiting_for_command = False
    input_buffer = ""

    def finalize_command():
        nonlocal current_command_offset, current_command, output_parts
        if current_command is not None and current_command_offset is not None:
            raw_output = "".join(output_parts)
            commands.append({
                "absolute_timestamp": base_ts + current_command_offset,
                "command": current_command,
                "output_raw": raw_output,
                "output_html": render_output_html(raw_output),
                "working_directory": current_cwd,
                "duration": None,  # computed later
            })

    for event in events:
        ts, event_type, data = event[0], event[1], event[2]

        if event_type == "o":
            # Extract working directory from OSC 7
            cwd_match = OSC7_CWD_RE.search(data)
            if cwd_match:
                current_cwd = cwd_match.group(1)

            # Detect prompt reset (terminal title set to root@...)
            title_match = TITLE_RE.search(data)
            if title_match and ROOT_TITLE_RE.match(title_match.group(1)):
                # New prompt — finalize previous command
                finalize_command()

                current_command = None
                current_command_offset = None
                output_parts = []
                waiting_for_command = True
                input_buffer = ""
                continue

            # Check for terminal title (command being executed)
            if waiting_for_command:
                cmd = extract_command_from_title(data)
                if cmd is not None:
                    current_command = cmd
                    current_command_offset = ts
                    waiting_for_command = False
                    output_parts = []
                    continue

            # Accumulate output if we have a command running
            if current_command is not None:
                output_parts.append(data)

        elif event_type == "i":
            if waiting_for_command:
                # Track input for fallback command reconstruction
                if data == "\r":
                    cmd = input_buffer.strip()
                    if cmd:
                        input_buffer = cmd
                elif data == "\x7f" or data == "\b":
                    # Backspace
                    input_buffer = input_buffer[:-1]
                elif data == "\x03":
                    # Ctrl+C - discard
                    input_buffer = ""
                elif len(data) == 1 and ord(data) >= 32:
                    input_buffer += data
                elif len(data) > 1 and not data.startswith("\x1b"):
                    # Paste event
                    input_buffer += data

    # Finalize last command
    finalize_command()

    # Compute durations
    for i in range(len(commands) - 1):
        commands[i]["duration"] = round(
            commands[i + 1]["absolute_timestamp"] - commands[i]["absolute_timestamp"],
            1,
        )

    return session_info, commands


def parse_data_directory(data_dir: str | Path) -> list[tuple[dict, list[dict]]]:
    """Parse all asciinema files in a directory.

    Returns list of (session_info, commands) tuples.
    """
    data_dir = Path(data_dir)
    results = []

    patterns = ["*.asciinema", "*.asciinema.gz"]
    files = []
    for pattern in patterns:
        files.extend(data_dir.glob(pattern))

    for filepath in sorted(files):
        try:
            session_info, commands = parse_session(filepath)
            if commands:
                results.append((session_info, commands))
        except Exception as e:
            print(f"Warning: failed to parse {filepath}: {e}")

    return results
