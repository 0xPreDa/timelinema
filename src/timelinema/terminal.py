"""Streaming terminal tokens and shell echo reconstruction.

Recording event boundaries are arbitrary: an escape sequence may cross them.
Only complete terminal tokens are passed to consumers.
"""
import re

import pyte

ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\)"
    r"|[PX^_].*?\x1b\\|[ -/]+[0-~]|(?![\[\]PX^_])[0-~])",
    re.DOTALL,
)
SGR_RE = re.compile(r"\x1b\[[0-?]*[ -/]*m")


def terminal_events(events):
    """Split output into text/control tokens, preserving completion timestamps."""
    pending = ""
    last_output_ts = 0
    for ts, kind, data in events:
        if kind != "o":
            yield [ts, kind, data]
            continue
        last_output_ts = ts
        data = pending + data
        pending = ""
        pos = 0
        while pos < len(data):
            esc = data.find("\x1b", pos)
            if esc < 0:
                yield [ts, kind, data[pos:]]
                break
            if esc > pos:
                yield [ts, kind, data[pos:esc]]
            match = ESCAPE_RE.match(data, esc)
            if match:
                yield [ts, kind, match.group()]
                pos = match.end()
            else:
                tail = data[esc:]
                next_escape = data.find("\x1b", esc + 1)
                incomplete = (tail == "\x1b"
                              or re.fullmatch(r"\x1b\[[0-?]*[ -/]*", tail)
                              or re.fullmatch(r"\x1b[ -/]+", tail)
                              or (len(tail) > 1 and tail[1] in "]PX^_"))
                # Corrupt/binary output must not turn one invalid escape into
                # an ever-growing pending buffer swallowing the recording.
                if (incomplete and len(tail) <= 65536
                        and (next_escape < 0 or next_escape == len(data) - 1)):
                    pending = tail
                    break
                pos = next_escape if next_escape >= 0 else esc + 1
                yield [ts, kind, data[esc:pos]]
    if pending:
        yield [last_output_ts, "o", pending]



def submission_prefix(data):
    """Split post-Enter cursor/control echo from the first printable output.

    Shells may move from the edited row to the end of a multiline command
    AFTER disabling bracketed paste, even in the same event as program output.
    """
    consumed = 0
    for _, _, token in terminal_events([[0, "o", data]]):
        if token.startswith("\x1b"):
            consumed += len(token)
            continue
        for char in token:
            if char >= " " and char != "\x7f":
                return data[:consumed], data[consumed:]
            consumed += len(char)
    return data, ""


def strip_escapes(text, keep_sgr=False):
    return "".join(
        data for _, _, data in terminal_events([[0, "o", text]])
        if not data.startswith("\x1b") or (keep_sgr and SGR_RE.fullmatch(data))
    )


class _CommandScreen(pyte.Screen):
    """Remember automatic wrapping separately from literal newlines."""
    def __init__(self, columns, lines):
        self.wrapped_rows = {}
        self.drawing = False
        super().__init__(columns, lines)

    def draw(self, data):
        self.drawing = True
        try:
            super().draw(data)
        finally:
            self.drawing = False

    def linefeed(self):
        if self.drawing:
            row = self.buffer[self.cursor.y]
            self.wrapped_rows[id(row)] = row
        super().linefeed()


class EchoScreen:
    """Replay line editor redraws, including completion menus and cursor edits."""
    def __init__(self, width=80, height=24):
        self.screen = _CommandScreen(max(1, min(width, 4096)), max(2, min(height, 1024)))
        # Leave space above the prompt for interactive history search menus.
        self.screen.cursor.y = self.screen.lines // 2
        self.stream = pyte.Stream(self.screen)
        self.prefix = None
        self.column = 0
        self.right_prompt = ()
        self.right_column = 0

    def feed(self, text):
        self.stream.feed(text)

    def anchor(self, *, column=None, row_index=None, capture_right=True):
        self.column = self.screen.cursor.x if column is None else column
        row = self.screen.buffer[self.screen.cursor.y if row_index is None else row_index]
        self.prefix = tuple(row[x].data for x in range(self.column))
        right = next((x for x in range(self.column, self.screen.columns)
                      if row[x].data.strip()), self.screen.columns)
        self.right_column = right
        self.right_prompt = (tuple(row[x].data for x in range(right, self.screen.columns))
                             if capture_right else ())

    def reanchor_prompt(self, pattern, *, multiline=False):
        """Locate a repainted prompt even when its command is already visible.

        Match rendered text, then translate its character offset back to cell
        coordinates (emoji and combining characters are not one cell each).
        """
        first_row = 0 if multiline else self.screen.cursor.y
        while first_row and id(self.screen.buffer[first_row - 1]) in self.screen.wrapped_rows:
            first_row -= 1
        for y in range(self.screen.cursor.y, first_row - 1, -1):
            row = self.screen.buffer[y]
            cells = [row[x].data for x in range(self.screen.columns)]
            match = pattern.match("".join(cells))
            if not match:
                continue
            length = 0
            for column, cell in enumerate(cells, 1):
                length += len(cell)
                if length == match.end():
                    self.anchor(column=column, row_index=y, capture_right=False)
                    return True
        return False

    def command(self):
        if self.prefix is None:
            return ""
        screen = self.screen
        # Find the actual prompt again after scrolling or an fzf redraw.
        starts = [y for y in range(screen.cursor.y + 1)
                  if tuple(screen.buffer[y][x].data for x in range(self.column)) == self.prefix]
        if not starts:
            return ""
        start = starts[-1] if self.prefix else starts[0]
        rows = []
        wraps = []
        for y in range(start, screen.cursor.y + 1):
            col = self.column if y == start else 0
            row = screen.buffer[y]
            end = screen.columns
            if (y == start and self.right_prompt
                    and tuple(row[x].data for x in range(self.right_column, end)) == self.right_prompt
                    and all(not row[x].data.strip() for x in range(max(col, self.right_column - 3), self.right_column))):
                end = self.right_column
            rows.append("".join(row[x].data for x in range(col, end)))
            wraps.append(id(row) in screen.wrapped_rows)
        # Enter often emits CR LF before disabling bracketed paste.
        while rows and not rows[-1].strip():
            rows.pop()
        result = ""
        for n, row in enumerate(rows):
            if n:
                if not wraps[n - 1]:
                    result += "\n"
                    if row.startswith("> "):
                        row = row[2:]
            result += row if wraps[n] and n + 1 < len(rows) else row.rstrip()
        return result.strip()


def shell_events(events):
    """Isolate shell markers without retaining a list entry for every SGR code."""
    parts = []
    current_ts = None
    for ts, kind, data in terminal_events(events):
        boundary = (kind != "o" or data.startswith("\x1b]")
                    or data in ("\x1b[?2004h", "\x1b[?2004l",
                                "\x1b[?1049h", "\x1b[?1049l", "\x1b[?1047h", "\x1b[?1047l",
                                "\x1b[?47h", "\x1b[?47l", "\x1b[?1000l", "\x1b[?1002l", "\x1b[?1006l"))
        if parts and (boundary or ts != current_ts):
            yield [current_ts, "o", "".join(parts)]
            parts = []
        if boundary:
            yield [ts, kind, data]
        else:
            parts.append(data)
            current_ts = ts
    if parts:
        yield [current_ts, "o", "".join(parts)]
