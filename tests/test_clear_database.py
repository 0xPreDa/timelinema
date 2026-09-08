import sqlite3
import tempfile
import unittest
from pathlib import Path

from timelinema import database
from timelinema.app import create_app


class ClearDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.db'
        self.recording = Path(self.tmp.name) / 'keep.cast'
        self.recording.write_text('recording bytes remain untouched')
        conn = database.init_db(self.path)
        for name in ('One', 'Two'):
            project = database.create_project(conn, name)
            sid = database.insert_session(conn, name + '.cast', name, 0, 80, 24, project)
            conn.execute('INSERT INTO commands (session_id, absolute_timestamp, command) VALUES (?, 0, ?)',
                         (sid, 'echo example'))
            conn.commit()
        conn.close()
        self.client = create_app(str(self.path), self.tmp.name).test_client()

    def counts(self):
        return self.client.get('/api/database').json

    def test_requires_exact_confirmation_and_json(self):
        before = self.counts()
        for payload in ({}, {'confirmation': 'delete'}, {'confirmation': True}, ['DELETE']):
            self.assertEqual(self.client.delete('/api/database', json=payload).status_code, 400)
        self.assertEqual(self.client.delete('/api/database', data={'confirmation': 'DELETE'}).status_code, 400)
        self.assertEqual(self.counts(), before)

    def test_clear_all_projects_keep_recordings_and_database_usable(self):
        self.assertEqual(self.client.delete('/api/database', json={'confirmation': 'DELETE'}).status_code, 200)
        self.assertEqual(self.counts(), {'projects': 1, 'sessions': 0, 'commands': 0})
        self.assertEqual(self.recording.read_text(), 'recording bytes remain untouched')
        conn = database.init_db(self.path)
        project = conn.execute('SELECT id, name FROM projects').fetchone()
        self.assertEqual(project['name'], 'Default')
        database.insert_session(conn, 'new.cast', 'New', 0, 80, 24, project['id'])
        conn.close()
        self.assertEqual(self.counts()['sessions'], 1)

    def test_requires_login_when_auth_enabled(self):
        client = create_app(str(self.path), self.tmp.name,
                            {'auth': {'password': 'test-only'}}).test_client()
        self.assertEqual(client.get('/api/database').status_code, 401)
        self.assertEqual(client.delete('/api/database', json={'confirmation': 'DELETE'}).status_code, 401)
        client.post('/api/auth/login', json={'password': 'test-only'})
        self.assertEqual(client.delete('/api/database', json={'confirmation': 'DELETE'}).status_code, 200)

    def test_failure_rolls_back_entire_clear(self):
        before = self.counts()
        conn = database.get_connection(self.path)
        conn.execute("CREATE TRIGGER prevent_clear BEFORE DELETE ON projects BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            database.clear_database(conn)
        conn.close()
        self.assertEqual(self.counts(), before)
