"""Submission regressions: cursor position must not truncate the editor buffer."""
import random
import unittest

from timelinema.prompt_detector import detect_strategy
from timelinema.terminal import shell_events

ON = '\x1b[?2004h'
OFF = '\x1b[?2004l'
PROMPT = '\x1b]2;user@host:/tmp\x07user@host:/tmp$ ' + ON


def parse(chunks, width=120):
    events = list(shell_events([[n, 'o', chunk] for n, chunk in enumerate(chunks)]))
    return detect_strategy(events, width, 40).detect(events), events


class MultilineSubmissionTests(unittest.TestCase):
    def test_complete_command_at_every_edited_row_and_event_partition(self):
        command = 'tool req -debug \\\n    -user "$USER" \\\n    -host "$HOST" \\\n    -template "example" \\\n    -id "final-argument"'
        rows = command.splitlines()
        for width in (80, 120, 211):
            for edited_row in range(len(rows)):
                distance = len(rows) - edited_row - 1
                move_up = f'\x1b[{distance}A' if distance else ''
                move_down = f'\x1b[{distance}B' if distance else ''
                stream = (PROMPT + command.replace('\n', '\r\n') + move_up + OFF
                          + move_down + '\r\n\x1b]2;tool req -debug\x07'
                          + 'program output\r\n' + PROMPT)
                rng = random.Random(42)
                random_chunks = []
                offset = 0
                while offset < len(stream):
                    size = rng.randint(1, 25)
                    random_chunks.append(stream[offset:offset + size])
                    offset += size
                for chunks in ([stream], list(stream), random_chunks):
                    with self.subTest(width=width, row=edited_row, chunks=len(chunks)):
                        boundaries, events = parse(chunks, width)
                        self.assertEqual([b.command for b in boundaries], [command])
                        boundary = boundaries[0]
                        self.assertEqual(''.join(e[2] for e in events[
                            boundary.output_start_index:boundary.output_end_index]), 'program output\r\n')

    def test_history_multiline_redraw_with_changed_prompt(self):
        command = 'arbitrary-tool --first value \\n    --second value \\n    --last final'
        command = command.replace('\\n', '\\\n')
        stream = (PROMPT + 'search' + '\x1b[?1000l' + OFF
                  + '\r\x1b[J[updated] host /tmp # '
                  + command.replace('\n', '\r\n') + OFF + '\r\n'
                  + '\x1b]2;/expanded/tool --first\x07'
                  + 'program output\r\n' + PROMPT)
        for chunks in ([stream], list(stream)):
            with self.subTest(fragmented=len(chunks) > 1):
                bs, _ = parse(chunks)
                self.assertEqual([b.command for b in bs], [command])

    def test_cancelled_menu_does_not_cancel_next_edited_command(self):
        command = 'example --option complete-value'
        for fragmented in (False, True):
            with self.subTest(fragmented=fragmented):
                chunks = [('o', PROMPT + 'search'), ('i', '\x03'),
                          ('o', '\x1b[?1000l' + OFF + '\r\x1b[Juser@host:/tmp$ '),
                          ('o', command + OFF + '\r\n\x1b]2;example --option\x07'),
                          ('o', 'result\r\n' + PROMPT)]
                events = []
                for kind, data in chunks:
                    for part in (list(data) if fragmented else [data]):
                        events.append([len(events), kind, part])
                events = list(shell_events(events))
                bs = detect_strategy(events, 120, 40).detect(events)
                self.assertEqual([b.command for b in bs], [command])

    def test_ctrl_c_at_shell_prompt_still_cancels_command(self):
        events = list(shell_events([[0, 'o', PROMPT + 'aborted'],
                                    [1, 'i', '\x03'],
                                    [2, 'o', OFF + '\r\n' + PROMPT]]))
        self.assertEqual(detect_strategy(events, 120, 40).detect(events), [])

    def test_shell_integration_with_arbitrary_prompt(self):
        mark = lambda letter: '\x1b]133;' + letter + '\x07'
        command = 'printf "%s" "first line\nsecond line\nlast line"'
        bs, _ = parse([mark('A') + '🚀 custom prompt → ' + mark('B'),
                       command.replace('\n', '\r\n'), '\x1b[2A', OFF,
                       '\x1b[2B\r\n', mark('C'), 'result\r\n', mark('D;0')])
        self.assertEqual([b.command for b in bs], [command])

    def test_builtin_output_in_same_event_does_not_extend_command(self):
        command = 'printf "%s" "one\ntwo"'
        bs, _ = parse([PROMPT, command.replace('\n', '\r\n'), '\x1b[A', OFF,
                       '\x1b[B\r\none\r\ntwo\r\n', '\r\n', PROMPT])
        self.assertEqual([b.command for b in bs], [command])

    def test_submission_at_end_of_recording(self):
        command = 'echo one \\\n    two'
        bs, _ = parse([PROMPT, command.replace('\n', '\r\n'), '\x1b[A', OFF, '\x1b[B\r\n'])
        self.assertEqual([b.command for b in bs], [command])

    def test_literal_trailing_backslashes_are_preserved(self):
        command = 'echo folder\\\\'
        bs, _ = parse([PROMPT, command, OFF, '\r\n\x1b]2;echo folder\\\\\x07', PROMPT])
        self.assertEqual([b.command for b in bs], [command])


if __name__ == '__main__':
    unittest.main()
