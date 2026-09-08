import io
import json
from pathlib import Path
import tempfile
import unittest

from timelinema import database
from timelinema.app import create_app, load_data
from timelinema.parser import parse_data_directory


def recording(command):
    return '\n'.join(map(json.dumps, [
        {'version': 2, 'width': 80, 'height': 24},
        [1, 'o', '\x1b]2;u@h:/tmp\x07u@h:/tmp$ \x1b[?2004h'],
        [2, 'o', command + '\x1b[?2004l\x1b]2;' + command + '\x07'],
        [3, 'o', 'result\r\n\x1b]2;u@h:/tmp\x07'],
    ])).encode()


class UploadStorageTests(unittest.TestCase):
    def test_upload_saved_in_subdirectory_and_reload_finds_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'test.db'
            conn = database.init_db(db)
            project = conn.execute('SELECT id FROM projects').fetchone()[0]
            conn.close()
            client = create_app(str(db), tmp).test_client()
            response = client.post('/api/upload', data={
                'project_id': str(project),
                'files': (io.BytesIO(recording('echo before')), '../../demo.asciinema'),
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['sessions_loaded'], 1)
            path = root / 'uploads' / 'demo.asciinema'
            self.assertTrue(path.is_file())
            self.assertFalse((root / 'demo.asciinema').exists())
            path.write_bytes(recording('echo after'))
            response = client.post('/api/reload', json={'project_id': project})
            self.assertEqual(response.json['sessions_loaded'], 1)
            conn = database.get_connection(db)
            self.assertEqual(conn.execute('SELECT command FROM commands').fetchone()[0], 'echo after')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
            conn.close()
            self.assertEqual(load_data(tmp, str(db)), 0)

    def test_upload_takes_precedence_over_legacy_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'uploads').mkdir()
            (root / 'demo.asciinema').write_bytes(recording('echo old'))
            (root / 'uploads' / 'demo.asciinema').write_bytes(recording('echo uploaded'))
            (root / 'manual.cast').write_bytes(recording('echo manual'))
            results = parse_data_directory(root)
            self.assertEqual(len(results), 2)
            self.assertEqual({c['command'] for _, commands in results for c in commands},
                             {'echo uploaded', 'echo manual'})
