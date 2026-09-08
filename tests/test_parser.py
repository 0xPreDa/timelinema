import gzip
import json
import tempfile
import unittest
from pathlib import Path

from timelinema.parser import load_asciinema, parse_session, is_valid_asciinema, render_output_html
from timelinema.prompt_detector import detect_strategy, InputOnlyStrategy
from timelinema.terminal import shell_events

ON = '\x1b[?2004h'
OFF = '\x1b[?2004l'
PROMPT = '\x1b]2;user@host:/tmp\x07\x1b]7;file://host/tmp\x07user@host:/tmp$ ' + ON

def title(s):
    return '\x1b]2;' + s + '\x07'

def detect(events, width=80):
    events = list(shell_events(events))
    return detect_strategy(events, width, 40).detect(events), events

def outputs(*chunks):
    return [[i, 'o', c] for i, c in enumerate(chunks)]

class ParserTests(unittest.TestCase):
    def test_cursor_history_and_delete(self):
        events = outputs(PROMPT, 'echo old', '\ruser@host:/tmp$ echo new',
                         '\x1b[3D\x1b[P', OFF, title('echo ew'), 'answer\r\n', PROMPT)
        bs, es = detect(events)
        self.assertEqual([b.command for b in bs], ['echo ew'])
        self.assertEqual(bs[0].working_directory, '/tmp')
        self.assertIn('answer', ''.join(e[2] for e in es[bs[0].output_start_index:bs[0].output_end_index]))

    def test_fragmented_private_sequences(self):
        raw = PROMPT + 'echo hello' + OFF + title('echo hello') + 'OK\r\n' + PROMPT
        expected, _ = detect(outputs(raw))
        actual, _ = detect(outputs(*raw))
        self.assertEqual([b.command for b in actual], ['echo hello'])
        self.assertEqual([b.command for b in actual], [b.command for b in expected])

    def test_malformed_escape_recovers_without_losing_output(self):
        chunks = ['binary \x1b\ufffd bytes', '\x1b]unterminated', PROMPT,
                  'echo recovered', OFF, title('echo recovered'), 'OK', PROMPT]
        bs, es = detect(outputs(*chunks))
        self.assertEqual([b.command for b in bs], ['echo recovered'])
        self.assertEqual(''.join(e[2] for e in es if e[1] == 'o'), ''.join(chunks))

    def test_unterminated_escape_has_bounded_buffer(self):
        chunks = ['\x1b]unterminated'] + ['x' * 4096] * 40 + [PROMPT, 'echo ok', OFF, title('echo ok')]
        bs, es = detect(outputs(*chunks))
        self.assertEqual([b.command for b in bs], ['echo ok'])
        self.assertLess(max(len(e[2]) for e in es), 80000)

    def test_menu_is_not_a_command(self):
        bs, _ = detect(outputs(PROMPT, '\x1b[?2026h\r883 certipy auth stale\x1b[?2026l',
                               OFF, '\r\x1b[2Kuser@host:/tmp$ ', 'certipy auth chosen',
                               OFF, title('certipy auth chosen'), 'OK', PROMPT))
        self.assertEqual([b.command for b in bs], ['certipy auth chosen'])

    def test_history_redraw_with_changed_prompt_and_command_in_same_event(self):
        before = '[🔴][10:00:00] host /tmp # '
        after = '[🔴][10:02:00] host /tmp # '
        command = 'lookupsid.py "$DOMAIN"/"$USER":"$PASSWORD"@"$DC_HOST" 520'
        # fzf enables bracketed paste on an empty menu row. On return, zsh
        # repaints the prompt (with an updated time) AND the selected command.
        chunks = [title('user@host:/tmp'), before, ON, 'lookup',
                  '\r\n', ON, '685 menu item', '\r\x1b[J',
                  '\x1b[?1000l', OFF, '\x1b[?25h',
                  '\r\x1b[J' + after + command, OFF, title(command[:35]),
                  'command output', PROMPT]
        for fragmented in (False, True):
            with self.subTest(fragmented=fragmented):
                events = outputs(*(''.join(chunks) if fragmented else chunks))
                bs, es = detect(events, width=100)
                self.assertEqual([b.command for b in bs], [command])
                self.assertIn('command output', ''.join(
                    e[2] for e in es[bs[0].output_start_index:bs[0].output_end_index]))

    def test_redraw_preserves_prompt_delimiters_inside_arguments(self):
        command = 'echo "keep # and $ and % "'
        bs, _ = detect(outputs(title('user@host:/tmp'), '[🔴][old] host /tmp # ', ON,
                               '\r\n', ON, '\r\x1b[J', '\x1b[?1000l', OFF,
                               '\r\x1b[J[🔴][new] host /tmp # ' + command,
                               OFF, title('echo'), PROMPT), width=160)
        self.assertEqual([b.command for b in bs], [command])

    def test_builtin_and_no_input_recording(self):
        bs, _ = detect(outputs(PROMPT, 'cd /elsewhere', OFF, '\r\n',
                               '\x1b]2;user@host:/elsewhere\x07',
                               '\x1b]7;file://host/elsewhere\x07user@host:/elsewhere$ ', ON,
                               'pwd', OFF, title('pwd'), '/elsewhere\r\n', PROMPT))
        self.assertEqual([b.command for b in bs], ['cd /elsewhere', 'pwd'])
        self.assertEqual([b.working_directory for b in bs], ['/tmp', '/elsewhere'])

    def test_command_and_output_in_same_event(self):
        bs, es = detect(outputs(PROMPT + 'echo ok' + OFF + title('echo ok') + 'ok\r\n' + PROMPT))
        self.assertEqual([b.command for b in bs], ['echo ok'])
        self.assertEqual(''.join(e[2] for e in es[bs[0].output_start_index:bs[0].output_end_index]), 'ok\r\n')

    def test_osc133_bel_and_st_output_only(self):
        for end in ('\x07', '\x1b\\'):
            m = lambda c: '\x1b]133;' + c + end
            bs, es = detect(outputs(m('A')+'$ '+m('B')+'echo ok\r\n'+m('C')+'ok\r\n'+m('D;0')))
            self.assertEqual([b.command for b in bs], ['echo ok'])
            self.assertEqual(''.join(e[2] for e in es[bs[0].output_start_index:bs[0].output_end_index]), 'ok\r\n')

    def test_long_wrapped_command(self):
        cmd = 'echo ' + 'a' * 1000
        bs, _ = detect(outputs(PROMPT, cmd, OFF, title(cmd[:70]), 'OK', PROMPT), 80)
        self.assertEqual(bs[0].command, cmd)

    def test_wrap_preserves_spaces_inside_quotes(self):
        cmd = 'echo "' + 'a' * 58 + '   words"'
        bs, _ = detect(outputs(PROMPT, cmd, OFF, title(cmd[:40]), 'OK', PROMPT))
        self.assertEqual(bs[0].command, cmd)

    def test_literal_multiline_command(self):
        cmd = "echo one \\\n> two"
        bs, _ = detect(outputs(PROMPT, cmd.replace('\n', '\r\n'), OFF, title('echo one'), 'OK', PROMPT))
        self.assertEqual(bs[0].command, "echo one \\\n two".replace('\n ', '\n'))

    def test_right_prompt_is_not_part_of_command(self):
        prompt = '\x1b]2;u@h:/tmp\x07u@h:/tmp$ \x1b[60C127 ↵\x1b[65D' + ON
        bs, _ = detect(outputs(prompt, 'echo ok', OFF, title('echo ok'), 'OK', PROMPT))
        self.assertEqual(bs[0].command, 'echo ok')

    def test_partial_shell_integration(self):
        bs, _ = detect(outputs(PROMPT, 'echo one', OFF, '\x1b]133;C\x07', title('echo one'),
                               'one', PROMPT, 'echo two', OFF, '\x1b]133;C\x07', 'two', PROMPT))
        self.assertEqual([b.command for b in bs], ['echo one', 'echo two'])

    def test_titleless_bash(self):
        prompt = 'u@h:/tmp$ ' + ON
        bs, _ = detect(outputs(prompt, 'echo a\r\n', OFF, 'a\r\n' + prompt,
                               'echo b\r\n', OFF, 'b\r\n' + prompt))
        self.assertEqual([b.command for b in bs], ['echo a', 'echo b'])

    def test_alternate_screen_menu(self):
        bs, _ = detect(outputs(PROMPT, '\x1b[?1049h', ON, '883 wrong menu entry',
                               '\x1b[?1049l', '\x1b[?1000l', OFF,
                               '\r\x1b[2Kuser@host:/tmp$ echo chosen',
                               OFF, title('echo chosen'), 'OK', PROMPT))
        self.assertEqual([b.command for b in bs], ['echo chosen'])

    def test_cancel_is_not_execution(self):
        es = outputs(PROMPT, 'do not run') + [[2, 'i', '\x03']] + outputs(OFF, PROMPT)
        bs, _ = detect(es)
        self.assertEqual(bs, [])

    def test_unaccepted_suggestion_erased(self):
        bs, _ = detect(outputs(PROMPT, 'echo\x1b[90m unwanted\x1b[0m\x1b[9D',
                               '\x1b[K', OFF, title('echo'), '\r\n', PROMPT))
        self.assertEqual(bs[0].command, 'echo')

    def test_input_only_editing_and_batched_enter(self):
        es = [[1, 'i', 'echo ac\x1b[Db\r'], [2, 'o', 'abc'], [3, 'i', '\x1b[A\r']]
        with self.assertWarns(UserWarning):
            bs, _ = detect(es)
        self.assertEqual([b.command for b in bs], ['echo abc'])

    def test_input_crlf_does_not_end_command_output(self):
        bs = InputOnlyStrategy().detect([[1, 'i', 'echo ok\r\n'], [2, 'o', 'ok\r\n']])
        self.assertEqual(len(bs), 1)
        self.assertEqual(bs[0].output_end_index, 2)

    def test_input_bracket_paste_split(self):
        es = [[i, 'i', c] for i,c in enumerate('\x1b[200~echo one\necho two\x1b[201~\r')]
        bs = InputOnlyStrategy().detect(es)
        self.assertEqual([b.command for b in bs], ['echo one\necho two'])

    def test_load_versions_and_gzip_magic(self):
        for version in (2, 3):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / 'recording.cast'
                h = {'version': version, 'timestamp': 1000, 'width': 80, 'height': 24,
                     'term': {'cols': 90, 'rows': 30}}
                raw = '\n'.join(map(json.dumps, [h, [1, 'o', PROMPT],
                       [2, 'o', 'echo ok'+OFF+title('echo ok')], [3,'o','OK'+PROMPT]]))
                p.write_bytes(gzip.compress(raw.encode()))
                loaded, es = load_asciinema(p)
                self.assertEqual([e[0] for e in es], [1,3,6] if version == 3 else [1,2,3])
                info, cmds = parse_session(p)
                self.assertEqual(info['width'], 90 if version == 3 else 80)
                self.assertEqual(cmds[0]['absolute_timestamp'], 1003 if version == 3 else 1002)
                self.assertGreaterEqual(cmds[0]['duration'], 0)
                self.assertEqual(is_valid_asciinema(p.read_bytes()), (True, True))

    def test_invalid_versions_and_v3_event(self):
        self.assertEqual(is_valid_asciinema(b'{"version":99}'), (False, False))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.cast'
            p.write_text('{"version":3}\n[1,"o","ok"]\ninvalid\n')
            with self.assertRaisesRegex(ValueError, 'line 3'):
                load_asciinema(p)

    def test_html_controls_and_escaping(self):
        html = render_output_html('\x1b[?25h\x1b[?7h\x1b[?2026l\x1b[31m<script>\x1b[0m')
        self.assertNotIn('[?25h', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('color:', html)

if __name__ == '__main__':
    unittest.main()
