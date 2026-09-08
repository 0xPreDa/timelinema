"""Optional regressions using a private, Git-ignored corpus and manifest.

Set TIMELINEMA_RECORDINGS_DIR to the directory containing the recordings and
regression-manifest.json. Public tests contain no client-derived fixtures.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import unittest

from timelinema.parser import load_asciinema
from timelinema.prompt_detector import detect_strategy
from timelinema.terminal import shell_events

ROOT = Path(os.environ.get('TIMELINEMA_RECORDINGS_DIR',
                          Path(__file__).resolve().parents[1] / 'uploads'))


class LocalRecordingTests(unittest.TestCase):
    def test_private_corpus_commands_and_event_partition_invariance(self):
        manifest = ROOT / 'regression-manifest.json'
        if not manifest.exists():
            self.skipTest('Private regression corpus is not available')
        recordings = json.loads(manifest.read_text())
        for index, record in enumerate(recordings):
            # Numeric labels and hashes keep client content out of test logs.
            with self.subTest(recording=index):
                path = ROOT / record['filename']
                self.assertTrue(path.is_file(), 'Private fixture missing')
                header, raw = load_asciinema(path)

                def extract(events):
                    events = list(shell_events(events))
                    boundaries = detect_strategy(
                        events, header['width'], header['height']).detect(events)
                    return [(b.command_offset, hashlib.sha256(b.command.encode()).hexdigest(),
                             hashlib.sha256(''.join(e[2] for e in events[
                                 b.output_start_index:b.output_end_index] if e[1] == 'o').encode()).hexdigest())
                            for b in boundaries]

                original = extract(raw)
                self.assertEqual([row[1] for row in original], record['commands'],
                                 'Private command baseline changed')
                rng = random.Random(493)
                fragmented = []
                for ts, kind, data in raw:
                    if kind == 'o':
                        cut = rng.randrange(len(data) + 1)
                        fragmented.extend([[ts, kind, data[:cut]], [ts, kind, data[cut:]]])
                    else:
                        fragmented.append([ts, kind, data])
                self.assertEqual(extract(fragmented), original)


if __name__ == '__main__':
    unittest.main()
