"""Detect shell execution boundaries without treating keystrokes as commands.

Shell integration markers are authoritative. Otherwise terminal titles and
bracketed-paste transitions delimit the shell editor. The editor's rendered
line (not history-search results or individual output fragments) supplies the
command. Input-only recordings have a deliberately conservative fallback.
"""
import re
import warnings
from dataclasses import dataclass
from urllib.parse import unquote

from .terminal import EchoScreen, strip_escapes, submission_prefix

TITLE_RE = re.compile(r"\x1b\][02];(.*?)(?:\x07|\x1b\\)", re.DOTALL)
PROMPT_TITLE_RE = re.compile(r"^[\w.-]+@[\w.-]+:")
OSC7_CWD_RE = re.compile(r"\x1b\]7;file://[^/]*(/.*?)(?:\x07|\x1b\\)")
OSC133_RE = re.compile(r"\x1b\]133;([A-D])(?:;[^\x07\x1b]*)?(?:\x07|\x1b\\)")
BRACKET_PASTE_ON = "\x1b[?2004h"
BRACKET_PASTE_OFF = "\x1b[?2004l"
VISUAL_PROMPT_RE = re.compile(
    r"^(?:[\w.-]+@[\w.-]+:\S+[#$%>] |[╰└]─[#$%>] |\[.*\].*[#$%] )$"
)

# Used only during recovery from an editor/menu redraw. Stop at the first
# prompt delimiter so a quoted "# " or "$ " in the command stays untouched.
VISUAL_PROMPT_PREFIX_RE = re.compile(
    r"^(?:[\w.-]+@[\w.-]+:\S+?[#$%>] |[╰└]─[#$%>] "
    r"|\[[^\r\n]*?\][^\r\n]*?[#$%] )"
)


@dataclass
class CommandBoundary:
    command: str
    command_offset: float
    output_start_index: int
    output_end_index: int
    working_directory: str | None = None


class ShellStrategy:
    name = "shell_stream"

    def __init__(self, width=80, height=24):
        self.width = width
        self.height = height

    def detect(self, events):
        integrated = any(e[1] == "o" and (m := OSC133_RE.fullmatch(e[2]))
                         and m.group(1) in "ABD" for e in events)
        boundaries = []
        echo = None
        pending = None  # (command, execution timestamp, output start, cwd)
        active = None
        cwd = ""
        prompt_cwd = ""
        editing = False
        awaiting_title = False
        cancelled = False
        alternate = False
        menu_teardown = False
        submission_open = False

        def finish(end):
            nonlocal active
            if active:
                cmd, ts, start, directory = active
                boundaries.append(CommandBoundary(cmd, ts, start, end, directory))
                active = None

        for i, (ts, kind, data) in enumerate(events):
            if kind == "r":
                match = re.fullmatch(r"(\d+)x(\d+)", data)
                if match:
                    self.width, self.height = map(int, match.groups())
                    if echo:
                        echo.screen.resize(lines=max(2, min(self.height, 1024)),
                                           columns=max(1, min(self.width, 4096)))
                continue
            if kind == "i":
                if editing and "\x03" in data:
                    cancelled = True
                continue
            if kind != "o":
                continue
            if data in ("\x1b[?1049h", "\x1b[?1047h", "\x1b[?47h"):
                alternate = True
                continue
            if data in ("\x1b[?1049l", "\x1b[?1047l", "\x1b[?47l"):
                alternate = False
                menu_teardown = True
                continue
            if alternate:
                continue
            if data in ("\x1b[?1000l", "\x1b[?1002l", "\x1b[?1006l"):
                menu_teardown = True
            cwd_match = OSC7_CWD_RE.fullmatch(data)
            if cwd_match:
                cwd = unquote(cwd_match.group(1))
                if echo and not editing:
                    prompt_cwd = cwd
                continue
            marker = OSC133_RE.fullmatch(data)
            title = TITLE_RE.fullmatch(data)
            prompt_title = title and PROMPT_TITLE_RE.match(title.group(1))

            if marker and marker.group(1) in "AD":
                finish(i)
                pending = None
                if marker.group(1) == "A":
                    echo = EchoScreen(self.width, self.height)
                    prompt_cwd = cwd
                    editing = False
                    cancelled = False
                    menu_teardown = False
                continue
            if prompt_title and not integrated:
                if pending:
                    active = pending
                finish(i)
                pending = None
                submission_open = False
                echo = EchoScreen(self.width, self.height)
                prompt_cwd = title.group(1).split(":", 1)[1]
                cwd = prompt_cwd
                editing = False
                awaiting_title = False
                cancelled = False
                menu_teardown = False
                continue
            if marker and marker.group(1) == "B":
                if echo is None:
                    echo = EchoScreen(self.width, self.height)
                echo.anchor()
                prompt_cwd = cwd
                editing = True
                continue
            if data == BRACKET_PASTE_ON:
                if echo is None:
                    finish(i)
                    echo = EchoScreen(self.width, self.height)
                    prompt_cwd = cwd
                row = echo.screen.buffer[echo.screen.cursor.y]
                at_prompt = (echo.prefix is not None and echo.screen.cursor.x >= echo.column
                             and tuple(row[x].data for x in range(echo.column)) == echo.prefix
                             and all(not row[x].data.strip()
                                     for x in range(echo.column, echo.screen.cursor.x)))
                if not editing or echo.prefix is None or at_prompt:
                    echo.anchor()
                editing = True
                cancelled = False
                continue
            if data == BRACKET_PASTE_OFF and echo:
                if menu_teardown:
                    # Ctrl-C inside the menu cancels the search, not the
                    # shell command subsequently edited at the restored prompt.
                    cancelled = False
                    # Interactive menu teardown is not shell execution.
                    pending = None
                    submission_open = False
                    menu_teardown = False
                    awaiting_title = True
                    continue
                # A completed history redraw may leave the cursor several
                # logical lines below a changed prompt. Wait for submission
                # before scanning earlier rows, avoiding partially repainted
                # prompts when recording events split the redraw.
                if awaiting_title and pending is None:
                    echo.reanchor_prompt(VISUAL_PROMPT_PREFIX_RE, multiline=True)
                cmd = echo.command()
                if cmd and not cancelled:
                    pending = (cmd, ts, i + 1, prompt_cwd)
                else:
                    pending = None
                awaiting_title = True
                submission_open = pending is not None
                # Keep the screen: fzf also toggles this mode while editing.
                continue
            if marker and marker.group(1) == "C":
                finish(i)
                cmd = pending[0] if pending else (echo.command() if echo else "")
                if cmd and not cancelled:
                    active = (cmd, ts, i + 1, prompt_cwd)
                pending = None
                editing = False
                echo = None
                continue
            if title and not prompt_title and not integrated and echo:
                cmd_title = title.group(1).strip()
                if cmd_title and not cmd_title.startswith("file://"):
                    # A full path in a title is an expansion performed by zsh.
                    # Preserve the typed command when its rendered echo exists.
                    cmd = pending[0] if pending else ""
                    if not cmd:
                        cmd = cmd_title
                    finish(i)
                    active = (cmd, ts, i + 1, prompt_cwd)
                    pending = None
                    editing = False
                    echo = None
                    awaiting_title = False
                continue
            if not integrated and echo is None and not active:
                # Output-only bash recordings may have no OSC metadata.
                cleaned = strip_escapes(data).split("\n")[-1].replace("\r", "")
                if VISUAL_PROMPT_RE.search(cleaned):
                    echo = EchoScreen(self.width, self.height)
                    prompt_cwd = cwd
            if echo:
                # Capture the complete submitted line after the shell's final
                # cursor moves, before any program output can enter the buffer.
                if submission_open and pending:
                    control, remainder = submission_prefix(data)
                    echo.feed(control)
                    command = echo.command()
                    if command:
                        pending = (command, *pending[1:])
                    if remainder:
                        submission_open = False
                    echo.feed(remainder)
                else:
                    echo.feed(data)
                if (awaiting_title and pending is None and strip_escapes(data).strip()
                        and echo.reanchor_prompt(VISUAL_PROMPT_PREFIX_RE)):
                    editing = True
                    awaiting_title = False
                # Prompt detection without bracketed paste (older shells).
                if not integrated and (not echo.prefix or awaiting_title):
                    row = "".join(echo.screen.buffer[echo.screen.cursor.y][x].data
                                  for x in range(echo.screen.columns)).rstrip() + " "
                    if VISUAL_PROMPT_RE.search(row):
                        if pending:
                            active = pending
                            finish(i)
                        echo.anchor()
                        editing = True
                        pending = None
                        awaiting_title = False
        if pending and not integrated:
            active = pending
        finish(len(events))
        return boundaries


class InputOnlyStrategy:
    name = "input_only"

    def detect(self, events):
        """Handle basic line editing; never fabricate unresolved history/tab input."""
        boundaries = []
        line = []
        cursor = 0
        uncertain = False
        paste = False
        pending_escape = ""
        active = None
        for i, (ts, kind, data) in enumerate(events):
            if kind != "i":
                continue
            data = pending_escape + data
            pending_escape = ""
            j = 0
            while j < len(data):
                c = data[j]
                if c == "\x1b":
                    match = re.match(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|O[A-Z])", data[j:])
                    if not match:
                        pending_escape = data[j:]
                        break
                    seq = match.group()
                    if seq == "\x1b[200~": paste = True
                    elif seq == "\x1b[201~": paste = False
                    elif seq in ("\x1b[D", "\x1bOD"): cursor = max(0, cursor - 1)
                    elif seq in ("\x1b[C", "\x1bOC"): cursor = min(len(line), cursor + 1)
                    elif seq in ("\x1b[H", "\x1bOH", "\x1b[1~"): cursor = 0
                    elif seq in ("\x1b[F", "\x1bOF", "\x1b[4~"): cursor = len(line)
                    elif seq == "\x1b[3~": del line[cursor:cursor + 1]
                    else: uncertain = True
                    j += len(seq)
                    continue
                if c in "\r\n" and not paste:
                    cmd = "".join(line).strip()
                    if active and (cmd or uncertain):
                        active.output_end_index = max(i, active.output_start_index)
                        boundaries.append(active)
                        active = None
                    if cmd and not uncertain:
                        active = CommandBoundary(cmd, ts, i + 1, len(events))
                    line, cursor, uncertain = [], 0, False
                elif c in "\b\x7f":
                    if cursor: del line[cursor - 1]; cursor -= 1
                elif c == "\x03": line, cursor, uncertain = [], 0, False
                elif c == "\x01": cursor = 0
                elif c == "\x05": cursor = len(line)
                elif c == "\x15": del line[:cursor]; cursor = 0
                elif c == "\x0b": del line[cursor:]
                elif c == "\x17":
                    while cursor and line[cursor - 1].isspace(): del line[cursor - 1]; cursor -= 1
                    while cursor and not line[cursor - 1].isspace(): del line[cursor - 1]; cursor -= 1
                elif c == "\t" and not paste: uncertain = True
                elif c >= " " or (paste and c in "\r\n\t"):
                    line.insert(cursor, c); cursor += 1
                j += 1
        if active:
            boundaries.append(active)
        return boundaries


def detect_strategy(events, width=80, height=24):
    if any(e[1] == "o" and (TITLE_RE.fullmatch(e[2]) or OSC133_RE.fullmatch(e[2])
                            or e[2] == BRACKET_PASTE_ON
                            or (len(e[2]) < 8192 and VISUAL_PROMPT_RE.search(strip_escapes(e[2])))) for e in events):
        return ShellStrategy(width, height)
    warnings.warn("No shell prompt markers found; using conservative input-only parsing.", stacklevel=2)
    return InputOnlyStrategy()
