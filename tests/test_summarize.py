import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from unittest.mock import patch

import summarize


class SummarizeTests(unittest.TestCase):
    def test_claude_command(self):
        self.assertEqual(summarize.claude_command('chosen-model'), [
            'claude', '-p', '--setting-sources', 'local', '--tools', '',
            '--strict-mcp-config', '--no-session-persistence', '--model', 'chosen-model',
            '--output-format', 'text'])

    def test_claude_backend(self):
        args = SimpleNamespace(backend='claude', claude_model='chosen-model')
        log = io.StringIO()
        with patch('summarize.subprocess.run', return_value=subprocess.CompletedProcess(
                [], 0, stdout='# Claude summary')) as run:
            self.assertEqual(summarize.ask_llm('claude', 'chosen-model', 'prompt', Path('/work'), log, ''),
                             '# Claude summary')
        run.assert_called_once_with(summarize.claude_command('chosen-model'),
                                    cwd=Path('/work'), input='prompt', text=True,
                                    timeout=1200, stdout=subprocess.PIPE, stderr=log, check=True)
        with patch('summarize.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'claude')):
            with self.assertRaises(subprocess.CalledProcessError):
                summarize.ask_llm('claude', 'chosen-model', 'prompt', Path('/work'), log, '')

    def test_anthropic_backend(self):
        class APIStatusError(Exception):
            pass

        class AuthenticationError(APIStatusError):
            pass

        class APIConnectionError(Exception):
            pass

        create = Mock(return_value=SimpleNamespace(stop_reason='end_turn', content=[
            SimpleNamespace(type='text', text='# Claude'), SimpleNamespace(type='other'),
            SimpleNamespace(type='text', text=' summary')]))
        factory = Mock(return_value=SimpleNamespace(beta=SimpleNamespace(
            messages=SimpleNamespace(create=create))))
        module = SimpleNamespace(Anthropic=factory, AuthenticationError=AuthenticationError,
                                 APIStatusError=APIStatusError, APIConnectionError=APIConnectionError)
        args = SimpleNamespace(backend='anthropic', claude_model='chosen-model')
        with patch.dict('sys.modules', {'anthropic': module}):
            self.assertEqual(summarize.ask_llm('anthropic', 'chosen-model', 'prompt', Path('/work'), io.StringIO(), ''),
                             '# Claude summary')
            factory.assert_called_once_with()
            create.assert_called_once_with(
                model='chosen-model', max_tokens=16000,
                betas=['server-side-fallback-2026-07-01'], fallbacks='default',
                messages=[{'role': 'user', 'content': 'prompt'}])
            create.return_value = SimpleNamespace(stop_reason='refusal')
            with self.assertRaisesRegex(ValueError, 'Claude declined to summarise this recording'):
                summarize.ask_llm('anthropic', 'chosen-model', 'prompt', Path('/work'), io.StringIO(), '')
            for error, message in [(AuthenticationError, 'Anthropic API key is invalid'),
                                   (APIStatusError, 'Anthropic API request failed'),
                                   (APIConnectionError, 'Could not connect to Anthropic API')]:
                create.side_effect = error('secret-key')
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, '^' + message + '$'):
                    summarize.ask_llm('anthropic', 'chosen-model', 'prompt', Path('/work'), io.StringIO(), '')

    def test_ask_llm_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            def child(command, **kwargs):
                (work / 'summary.md').write_text('answer')
            for model in ('', 'chosen'):
                log = io.StringIO()
                with patch('summarize.audio_duration', return_value=125), patch('summarize.ensure_speech_model'), patch('summarize.subprocess.run', side_effect=child) as run:
                    self.assertEqual(summarize.ask_llm('codex', model, 'prompt', work, log, ''), 'answer')
                command = ['codex', 'exec', '--ignore-user-config', '--skip-git-repo-check',
                           '--ephemeral', '-s', 'read-only', '-c', 'approval_policy="never"']
                if model:
                    command += ['-m', model]
                run.assert_called_once_with(command + ['-o', str(work / 'summary.md'), '-'],
                                            cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                            check=True, input='prompt', text=True, timeout=1200)

    def test_ask_llm_proxy_and_defaults(self):
        response = io.StringIO(json.dumps({'choices': [{'message': {'content': 'answer'}}]}))
        with patch.dict('os.environ', {'CLIPROXY_API_KEY': 'test'}), patch(
                'summarize.urllib.request.urlopen', return_value=response) as request:
            self.assertEqual(summarize.ask_llm('proxy', '', 'prompt', Path('/work'),
                                                io.StringIO(), 'http://localhost/v1/'), 'answer')
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, 'http://localhost/v1/chat/completions')
        self.assertEqual(json.loads(req.data), {'model': 'gpt-5', 'temperature': 0.2,
                                               'messages': [{'role': 'user', 'content': 'prompt'}]})
        self.assertEqual(req.get_header('Authorization'), 'Bearer test')
        self.assertEqual(request.call_args.kwargs, {'timeout': 1200})
        self.assertEqual(summarize.default_model('claude', ''), 'opus')
        with patch('tools.anthropic_tier_model', return_value='claude-opus-5-5'):
            self.assertEqual(summarize.default_model('anthropic', ''), 'claude-opus-5-5')

    @staticmethod
    def bundle(texts):
        return ('## Glossary\n- Rohan\n\n## Speakers\n\n'
                'Speaker 1 is the person who recorded (microphone); other speakers came through the call audio.\n\n## Segments\n\n') + '\n\n'.join(
            f'[{i}:00.0] Speaker 1\n  Hinglish: {text}\n  English:  English {i}'
            for i, text in enumerate(texts)) + '\n'

    def test_correction_parsing_and_guard(self):
        bundle = self.bundle(['abcdefghij'] * 5)
        # Timestamp matching, unordered output, both inclusive boundaries and both rejections.
        output = ('[1:00.0] Speaker 1: ' + 'b' * 16 + '\n'
                  '[0:00.0] Speaker 1: ' + 'a' * 6 + '\n'
                  '[2:00.0] Speaker 1: short\n'
                  '[3:00.0] Speaker 1: ' + 'c' * 17)
        with patch('summarize.ask_llm', return_value=output), contextlib.redirect_stdout(io.StringIO()):
            result, transcript, counts = summarize.correct_bundle(bundle, 'fake', '', Path('/work'), io.StringIO(), '')
        self.assertEqual(counts, dict(accepted=2, rejected=2, missing=1))
        self.assertIn('Hinglish: aaaaaa\n  English:  English 0', result)
        self.assertIn('[2:00.0] Speaker 1: abcdefghij', transcript)
        self.assertIn('[4:00.0] Speaker 1: abcdefghij', transcript)

    def test_correction_chunk_failure_and_fake(self):
        bundle = self.bundle(['a' * 6000] * 3)
        chunks = summarize.correction_chunks(list(summarize.SEG.finditer(bundle)))
        self.assertEqual([len(chunk) for chunk in chunks], [1, 1, 1])
        log, events = io.StringIO(), io.StringIO()
        real_ask = summarize.ask_llm
        calls = []
        def ask(provider, model, prompt, work, log, url):
            calls.append(prompt)
            if len(calls) == 2:
                raise RuntimeError('failed test chunk')
            return real_ask(provider, model, prompt, work, log, url)
        with patch('summarize.ask_llm', side_effect=ask), contextlib.redirect_stdout(events):
            result, transcript, counts = summarize.correct_bundle(bundle, 'fake', '', Path('/work'), log, '')
        self.assertEqual(result, bundle)
        self.assertEqual(counts, dict(accepted=2, rejected=0, missing=1))
        self.assertIn('failed test chunk', log.getvalue())
        self.assertTrue(all('## Glossary\n- Rohan' in prompt for prompt in calls))
        self.assertTrue(all('## Speakers' in prompt for prompt in calls))
        self.assertIn('## Speakers', result)
        self.assertEqual([json.loads(line)['message'] for line in events.getvalue().splitlines()],
                         ['chunk 1/3', 'chunk 2/3', 'chunk 3/3'])

    def test_old_flags_parse(self):
        for provider, flag in [('codex', '--codex-model'), ('claude', '--claude-model'),
                               ('anthropic', '--claude-model'), ('proxy', '--proxy-model')]:
            args = summarize.parser().parse_args(['memo.wav', '--backend', provider, flag, 'old'])
            self.assertEqual(args.summary_backend, provider)
            self.assertEqual(getattr(args, flag[2:].replace('-', '_')), 'old')
            self.assertEqual(args.summary_model, '')
            self.assertEqual(args.correct_backend, 'none')
        args = summarize.parser().parse_args(['memo.wav', '--summary-backend', 'fake',
                                             '--summary-model', 'summary', '--correct-backend', 'claude',
                                             '--correct-model', 'correct'])
        self.assertEqual((args.summary_model, args.correct_model), ('summary', 'correct'))

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
        self.check_fake_pipeline(False)

    def test_corrected_summary_pipeline(self):
        self.check_fake_pipeline(True)

    def check_fake_pipeline(self, correct):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            audio = root / 'memo.m4a'
            audio.write_bytes(b'fake audio')
            meetings = root / 'Meetings'
            calls = []

            def child(command, **kwargs):
                calls.append(command)
                work = Path(kwargs['cwd'])
                kwargs['stdout'].write('child output\n')
                if command[2].endswith('transcribe.py'):
                    kwargs['stdout'].write('detected 2 speakers\n')
                    (work / 'memo.for-llm.md').write_text('raw bundle')
                    (work / 'memo.English.txt').write_text('English')
                else:
                    (work / 'memo.Hinglish.glossary-fixed.txt').write_text('Hinglish fixed')
                    (work / 'memo.for-llm.names-fixed.md').write_text(self.bundle(['misheard words']) if correct else self.bundle(['names fixed']))
                return subprocess.CompletedProcess(command, 0)

            output = io.StringIO()
            real_ask = summarize.ask_llm
            def ask(provider, model, prompt, work, log, url):
                self.assertIn('## Speakers', prompt)
                self.assertIn('Speaker 1 is the person who recorded (microphone); other speakers came through the call audio.', prompt)
                if prompt.startswith(summarize.CORRECTION_PROMPT):
                    return '[0:00.0] Speaker 1: repaired words'
                self.assertIn('Hinglish: repaired words' if correct else self.bundle(['names fixed']), prompt)
                if correct:
                    self.assertIn('English:  English 0', prompt)
                    self.assertNotIn('misheard words', prompt)
                return real_ask(provider, model, prompt, work, log, url)
            flags = ['--correct-backend', 'fake', '--correct-model', 'repair'] if correct else []
            with patch('summarize.ask_llm', side_effect=ask), patch('sys.argv', [
                    'summarize.py', str(audio), '--meetings-dir', str(meetings),
                    '--backend', 'fake'] + flags), patch('summarize.audio_duration', return_value=125), patch('summarize.ensure_speech_model'), patch('summarize.subprocess.run', side_effect=child), \
                    patch('summarize.urllib.request.urlopen', side_effect=AssertionError('network')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(summarize.main(), 0)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([event['stage'] for event in events],
                             ['transcribing', 'cleaning'] + (['correcting'] if correct else []) +
                             ['summarizing', 'organizing', 'done'])
            folder = Path(events[-1]['folder'])
            self.assertEqual({p.name for p in folder.iterdir()},
                             {'audio.m4a', 'summary.md', 'transcript.hinglish.txt',
                              'transcript.english.txt', 'transcript.for-llm.md', 'meta.json'} |
                             ({'transcript.corrected.txt'} if correct else set()))
            self.assertEqual((folder / 'audio.m4a').read_bytes(), b'fake audio')
            self.assertEqual((folder / 'transcript.hinglish.txt').read_text(), 'Hinglish fixed')
            self.assertEqual((folder / 'transcript.english.txt').read_text(), 'English')
            self.assertEqual((folder / 'transcript.for-llm.md').read_text(),
                             self.bundle(['misheard words']) if correct else self.bundle(['names fixed']))
            if correct:
                self.assertEqual((folder / 'transcript.corrected.txt').read_text(),
                                 '[0:00.0] Speaker 1: repaired words')
            meta = json.loads((folder / 'meta.json').read_text())
            self.assertEqual(meta['speakers'], 2)
            self.assertEqual(meta['duration_s'], 125)
            self.assertEqual(meta['backend'], 'fake')
            self.assertIsNone(meta['model'])
            self.assertEqual(meta['summary_backend'], meta['backend'])
            self.assertEqual(meta['summary_model'], meta['model'])
            self.assertEqual(meta['correct_backend'], 'fake' if correct else 'none')
            self.assertEqual(meta['correct_model'], 'repair' if correct else '')
            self.assertEqual(meta['correction'], dict(accepted=int(correct), rejected=0, missing=0))
            self.assertEqual(meta['source_file'], str(audio))
            recorded = datetime.fromisoformat(meta['recorded_at'])
            self.assertEqual((meetings / 'INDEX.md').read_text(), summarize.index_line(
                meetings, folder, 'Test meeting', recorded, 125, 2))
            self.assertEqual(list((meetings / '_work').iterdir()), [])
            self.assertEqual(len(calls), 2)

    def test_failure_keeps_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            audio = root / 'memo.wav'
            audio.touch()
            output = io.StringIO()
            with patch('sys.argv', ['summarize.py', str(audio), '--meetings-dir', str(root)]), \
                    patch('summarize.ensure_speech_model'), patch('summarize.subprocess.run', side_effect=RuntimeError('child failed')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(summarize.main(), 1)
            event = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(event['stage'], 'error')
            self.assertIn('child failed', Path(event['log']).read_text())


if __name__ == '__main__':
    unittest.main()
