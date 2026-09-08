import json
import tempfile
import unittest
from pathlib import Path

from timelinema import database
from timelinema.app import create_app, load_data

class ReloadTests(unittest.TestCase):
    def test_reload_replaces_commands_preserving_session_and_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp)/'test.db')
            path = Path(tmp)/'demo.cast'
            def write(command):
                path.write_text('\n'.join(map(json.dumps, [
                    {'version': 2, 'width': 80, 'height': 24},
                    [1, 'o', '\x1b]2;u@h:/tmp\x07u@h:/tmp$ \x1b[?2004h'],
                    [2, 'o', command+'\x1b[?2004l\x1b]2;'+command+'\x07'],
                    [3, 'o', 'result\r\n\x1b]2;u@h:/tmp\x07'],
                ])))
            write('echo before')
            self.assertEqual(load_data(tmp, db), 1)
            conn = database.get_connection(db)
            before = dict(conn.execute('SELECT * FROM sessions').fetchone())
            conn.close()
            write('echo after')
            self.assertEqual(load_data(tmp, db), 0)
            client = create_app(db_path=db, data_dir=tmp).test_client()
            response = client.post('/api/reload', json={'project_id': before['project_id']})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['sessions_loaded'], 1)
            conn = database.get_connection(db)
            after = dict(conn.execute('SELECT * FROM sessions').fetchone())
            self.assertEqual(after['id'], before['id'])
            self.assertEqual(after['project_id'], before['project_id'])
            self.assertEqual([r[0] for r in conn.execute('SELECT command FROM commands')], ['echo after'])
            conn.close()
            write('echo other-project')
            self.assertEqual(load_data(tmp, db, project_id=999, reparse=True), 0)

    def test_refresh_rolls_back_on_failed_insert(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = database.init_db(Path(tmp)/'test.db')
            session_id = database.insert_session(conn, 'demo.cast', 'old', 0, 80, 24)
            info = dict(title='new', start_timestamp=0, width=90, height=30)
            with self.assertRaises(Exception):
                database.replace_session_commands(conn, session_id, info, [{'command': 'incomplete'}])
            self.assertEqual(conn.execute('SELECT title FROM sessions').fetchone()[0], 'old')
            conn.close()
