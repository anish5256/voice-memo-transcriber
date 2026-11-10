import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import summarize


class SummarizeTests(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(summarize.extract_title('\n# .. Sales / Plan: Q?  ###\ntext', 'fallback'),
                         'Sales Plan Q')
        self.assertEqual(summarize.sanitize_title(' ... /\\:*?"<>| Hello   world '), 'Hello world')
        self.assertEqual(summarize.extract_title('# /:*?\n', '..memo'), 'memo')
        self.assertEqual(summarize.extract_title('No heading', 'memo'), 'memo')
        self.assertEqual(summarize.extract_title('', '...'), 'Untitled meeting')
        self.assertEqual(len(summarize.sanitize_title('a' * 100)), 60)

    def test_folder_uniqueness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            date = datetime(2026, 5, 7, 12, 30)
            folders = [summarize.meeting_folder(root, date, 'Plan / review') for _ in range(3)]
            self.assertEqual(folders[0].relative_to(root).as_posix(),
                             '2026/05/2026-05-07 1230 - Plan review')
            self.assertEqual(folders[1].name, folders[0].name + ' (2)')
            self.assertEqual(folders[2].name, folders[0].name + ' (3)')

    def test_index_line(self):
        root = Path('/meetings')
        folder = root / '2026/05/2026-05-07 1230 - Plan (2)'
        self.assertEqual(summarize.index_line(root, folder, 'Plan', datetime(2026, 5, 7, 12, 30),
                                              125, 2),
                         '- 2026-05-07 12:30 — [Plan](2026/05/2026-05-07%201230%20-%20Plan%20%282%29/summary.md) — 2 min, 2 speakers\n')
        for duration, speakers, expected in [(24.6, 1, '25 s, 1 speaker'),
                                             (59.6, 2, '60 s, 2 speakers'),
                                             (60, 1, '1 min, 1 speaker')]:
            with self.subTest(duration=duration, speakers=speakers):
                self.assertTrue(summarize.index_line(
                    root, folder, 'Plan', datetime(2026, 5, 7, 12, 30),
                    duration, speakers).endswith(f' — {expected}\n'))

    def test_fake_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            audio = root / 'memo.m4a'
            audio.write_bytes(b'fake audio')
            meetings = root / 'Meetings'
            calls = []

            def child(command, **kwargs):
                calls.append(command)
                if command[0] == 'ffprobe':
                    return subprocess.CompletedProcess(command, 0, stdout='125.0\n')
                work = Path(kwargs['cwd'])
                kwargs['stdout'].write('child output\n')
                if command[2].endswith('transcribe.py'):
                    kwargs['stdout'].write('detected 2 speakers\n')
                    (work / 'memo.for-llm.md').write_text('raw bundle')
                    (work / 'memo.English.txt').write_text('English')
                else:
                    (work / 'memo.Hinglish.glossary-fixed.txt').write_text('Hinglish fixed')
                    (work / 'memo.for-llm.names-fixed.md').write_text('names fixed')
                return subprocess.CompletedProcess(command, 0)

            output = io.StringIO()
            with patch('sys.argv', ['summarize.py', str(audio), '--meetings-dir', str(meetings),
                                    '--backend', 'fake']), patch('summarize.subprocess.run', side_effect=child), \
                    patch('summarize.urllib.request.urlopen', side_effect=AssertionError('network')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(summarize.main(), 0)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([event['stage'] for event in events],
                             ['transcribing', 'cleaning', 'summarizing', 'organizing', 'done'])
            folder = Path(events[-1]['folder'])
            self.assertEqual({p.name for p in folder.iterdir()},
                             {'audio.m4a', 'summary.md', 'transcript.hinglish.txt',
                              'transcript.english.txt', 'transcript.for-llm.md', 'meta.json'})
            self.assertEqual((folder / 'audio.m4a').read_bytes(), b'fake audio')
            self.assertEqual((folder / 'transcript.hinglish.txt').read_text(), 'Hinglish fixed')
            self.assertEqual((folder / 'transcript.english.txt').read_text(), 'English')
            self.assertEqual((folder / 'transcript.for-llm.md').read_text(), 'names fixed')
            meta = json.loads((folder / 'meta.json').read_text())
            self.assertEqual(meta['speakers'], 2)
            self.assertEqual(meta['duration_s'], 125)
            self.assertEqual(meta['backend'], 'fake')
            self.assertIsNone(meta['model'])
            self.assertEqual(meta['source_file'], str(audio))
            recorded = datetime.fromisoformat(meta['recorded_at'])
            self.assertEqual((meetings / 'INDEX.md').read_text(), summarize.index_line(
                meetings, folder, 'Test meeting', recorded, 125, 2))
            self.assertEqual(list((meetings / '_work').iterdir()), [])
            self.assertEqual(len(calls), 3)

    def test_failure_keeps_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            audio = root / 'memo.wav'
            audio.touch()
            output = io.StringIO()
            with patch('sys.argv', ['summarize.py', str(audio), '--meetings-dir', str(root)]), \
                    patch('summarize.subprocess.run', side_effect=RuntimeError('child failed')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(summarize.main(), 1)
            event = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(event['stage'], 'error')
            self.assertIn('child failed', Path(event['log']).read_text())


if __name__ == '__main__':
    unittest.main()
