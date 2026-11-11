import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import clean
import summarize


class CalendarTests(unittest.TestCase):
    EVENT = dict(title='Planning / review:', start='2026-11-11T12:30:00+05:30',
                 end='2026-11-11T13:30:00+05:30', organizer=dict(name='Organizer'),
                 attendees=[dict(name='Rohan'), dict(email='asha@example.com')], extra='preserved')
    BUNDLE = ('## Glossary\n- Product\n\n## Speakers\nSpeaker 1 records.\n\n'
              '## Segments\n\n[0:00.0] Speaker 1\n  Hinglish: Rohan meeting hai\n'
              '  English:  Rohan has a meeting\n')

    def pipeline(self, live=False, explicit=False, payload=None, correct=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / 'session'
            session.mkdir()
            (session / 'session.json').write_text(json.dumps(dict(started_at='2026-11-11T12:30:00+05:30')))
            (session / 'event.json').write_text(json.dumps(dict(title='Wrong event')))
            event_path = root / 'event.json' if explicit else session / 'event.json'
            event_path.write_text(payload if payload is not None else json.dumps(self.EVENT))
            audio = root / 'memo.m4a'
            audio.write_bytes(b'audio')
            prompts, warnings = [], []

            def files(work, stem):
                (work / f'{stem}.for-llm.md').write_text(self.BUNDLE)
                (work / f'{stem}.English.txt').write_text('English')
                (work / 'pieces.json').write_text('[[0, 1, 1]]')

            def finalize(session, work, log):
                recording = session / 'recording.m4a'
                recording.write_bytes(b'audio')
                files(work, 'recording')
                return recording, 1

            def child(command, **kwargs):
                if command[0] == 'ffprobe':
                    warnings.append(kwargs['stderr'].name)
                    return subprocess.CompletedProcess(command, 0, stdout='10')
                work = Path(kwargs['cwd'])
                if command[2].endswith('transcribe.py'):
                    files(work, 'memo')
                else:
                    with patch('sys.argv', ['clean.py', command[3]]), contextlib.redirect_stdout(io.StringIO()):
                        clean.main()
                return subprocess.CompletedProcess(command, 0)

            real_ask = summarize.ask_llm
            def ask(provider, model, prompt, work, log, url):
                prompts.append(prompt)
                warnings.append(log.flush() or Path(log.name).read_text())
                return real_ask(provider, model, prompt, work, log, url)

            argv = ['summarize.py'] + (['--live-session', str(session)] if live else [str(audio)])
            argv += ['--meetings-dir', str(root / 'Meetings'), '--backend', 'fake']
            if explicit:
                argv += ['--event-json', str(event_path)]
            if correct:
                argv += ['--correct-backend', 'fake']
            output = io.StringIO()
            with patch('sys.argv', argv), patch('summarize.subprocess.run', side_effect=child), patch(
                    'summarize.ask_llm', side_effect=ask), patch.dict(
                    'sys.modules', {'live_transcribe': SimpleNamespace(finalize=finalize)}), contextlib.redirect_stdout(output):
                self.assertEqual(summarize.main(), 0, output.getvalue())
            result = json.loads(output.getvalue().splitlines()[-1])
            folder = Path(result['folder'])
            meta = json.loads((folder / 'meta.json').read_text())
            # The saved bundle carries the meeting/attendee context when an event is linked.
            expected = summarize.event_context(self.BUNDLE, meta['event']) if 'event' in meta else self.BUNDLE
            self.assertEqual((folder / 'transcript.for-llm.md').read_text(), expected)
            return result, meta, prompts, warnings

    def test_event_flag_and_session_both_prompt_paths(self):
        for live, explicit in [(False, True), (True, False), (True, True)]:
            with self.subTest(live=live, explicit=explicit):
                result, meta, prompts, _ = self.pipeline(live, explicit)
                self.assertEqual(result['title'], 'Planning review')
                self.assertTrue(Path(result['folder']).name.endswith(' - Planning review'))
                self.assertEqual(meta['event'], self.EVENT)
                self.assertEqual(len(prompts), 2)
                for prompt in prompts:
                    self.assertLess(prompt.index('## Speakers\nSpeaker'), prompt.index('## Meeting'))
                    self.assertLess(prompt.index('## Meeting'), prompt.index('## Segments'))
                    for text in ['Planning / review:', '2026-11-11 12:30+05:30',
                                 '2026-11-11 13:30+05:30', 'Organizer: Organizer',
                                 'Attendees: Rohan, asha', 'Calendar attendee (context only): "asha"']:
                        self.assertIn(text, prompt)
                    self.assertEqual(clean.glossary_rules(prompt), [])

    def test_malformed_ignored_and_explicit_wins(self):
        for payload in ['{broken', '[]', 'null']:
            with self.subTest(payload=payload):
                result, meta, prompts, warnings = self.pipeline(True, True, payload)
                self.assertEqual(result['title'], 'Test meeting')
                self.assertNotIn('event', meta)
                self.assertTrue(any('Warning: ignoring calendar event' in line for line in warnings))
                self.assertTrue(all('## Meeting' not in prompt for prompt in prompts))

    def test_no_event_unchanged(self):
        result, meta, prompts, _ = self.pipeline()
        self.assertEqual(result['title'], 'Test meeting')
        self.assertNotIn('event', meta)
        self.assertTrue(all('## Meeting' not in prompt for prompt in prompts))
        self.assertEqual(summarize.event_context(self.BUNDLE, None), self.BUNDLE)

    def test_lenient_fields_and_context_cannot_make_rules(self):
        for event in [{}, dict(title=3, attendees=[None, 4, {}, dict(name='X (often heard as "Y")')],
                              organizer=[], start='bad date', end=4)]:
            bundle = summarize.event_context(self.BUNDLE, event)
            glossary = bundle.split('## Glossary')[1].split('## Speakers')[0]
            self.assertEqual(clean.glossary_rules(glossary), [])
            self.assertIn('## Meeting', bundle)
        result, meta, _, _ = self.pipeline(False, True, '{}', correct=False)
        self.assertEqual(meta['event'], {})
        self.assertEqual(result['title'], 'memo')
