"""Offline coverage for tier discovery and bounded retired-model recovery."""
import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import tools
import summarize
import live_transcribe as live


class ModelTests(unittest.TestCase):
    def setUp(self):
        tools.codex_cheap_model.cache_clear()
        tools.anthropic_models.cache_clear()

    def tearDown(self):
        tools.codex_cheap_model.cache_clear()
        tools.anthropic_models.cache_clear()

    def test_cli_tiers_and_catalog(self):
        for value in ('', 'tier:default'):
            self.assertEqual(tools.resolve_model('claude', value), 'opus')
            self.assertEqual(tools.resolve_model('codex', value), '')
        self.assertEqual(tools.resolve_model('claude', 'tier:cheap'), 'haiku')
        catalog = [dict(slug=slug, description=description, visibility=visibility)
                   for slug, description, visibility in [
                       ('gpt-5.6-mini', 'Efficient', 'list'),
                       ('gpt-6-luna', '', 'list'),
                       ('gpt-6.1-sol', 'Fast and affordable', 'list'),
                       ('gpt-7-luna', 'Fast', 'hide'),
                       ('gpt-8-sol', 'Workhorse', 'list')]]
        with patch('tools.subprocess.run', return_value=NS(stdout=json.dumps({"models": catalog}))) as run:
            for _ in range(2):
                self.assertEqual(tools.resolve_model('codex', 'tier:cheap'), 'gpt-6.1-sol')
            run.assert_called_once()
        self.assertEqual(tools.select_codex_cheap([]), '')

    def test_catalog_failure_cached(self):
        for error in (OSError('missing'), subprocess.CalledProcessError(1, 'codex')):
            tools.codex_cheap_model.cache_clear()
            with patch('tools.subprocess.run', side_effect=error) as run:
                self.assertEqual(tools.resolve_model('codex', 'tier:cheap'), '')
                self.assertEqual(tools.resolve_model('codex', 'tier:cheap'), '')
                run.assert_called_once()
        tools.codex_cheap_model.cache_clear()
        with patch('tools.subprocess.run', return_value=NS(stdout='not json')):
            self.assertEqual(tools.resolve_model('codex', 'tier:cheap'), '')

    def test_sdk_tiers_and_failure(self):
        listing = Mock(return_value=[NS(id=name) for name in (
            'claude-haiku-5-5', 'claude-haiku-6-1', 'claude-opus-6', 'claude-opus-5-6')])
        module = NS(Anthropic=Mock(return_value=NS(models=NS(list=listing))))
        with patch.dict('sys.modules', anthropic=module):
            self.assertEqual(tools.resolve_model('anthropic', 'tier:cheap'), 'claude-haiku-6-1')
            self.assertEqual(tools.resolve_model('anthropic', ''), 'claude-opus-6')
            self.assertEqual(tools.resolve_model('anthropic', 'tier:default'), 'claude-opus-6')
            self.assertEqual(listing.call_count, 1)
            tools.anthropic_models.cache_clear()
            listing.side_effect = RuntimeError('offline')
            self.assertEqual(tools.resolve_model('anthropic', 'tier:cheap'), 'claude-haiku-5-5')
            self.assertEqual(tools.resolve_model('anthropic', ''), 'claude-opus-5-5')

    def test_cli_retry_and_notice(self):
        for provider, requested, diagnostic, used in (
            ('claude', 'claude-haiku-retired', '[claude-code:unrecognized_model]', 'haiku'),
            ('claude', 'old-opus', "doesn't recognize the model", 'opus'),
            ('codex', 'old-mini', 'Model metadata for `old-mini` not found', 'gpt-6-luna'),
            ('codex', 'old-large', 'model_not_found', 'default')):
            with self.subTest(provider=provider, requested=requested), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                calls = []
                def run(command, **kwargs):
                    calls.append(command)
                    if len(calls) == 1:
                        kwargs['stderr' if provider == 'claude' else 'stdout'].write(diagnostic)
                        raise subprocess.CalledProcessError(1, command)
                    (work / 'summary.md').write_text('answer')
                    return NS(stdout='answer')
                output = io.StringIO()
                with patch('summarize.subprocess.run', side_effect=run), patch('tools.codex_cheap_model', return_value='gpt-6-luna'), contextlib.redirect_stdout(output):
                    self.assertEqual(summarize.ask_llm(provider, requested, 'prompt', work, io.StringIO(), ''), 'answer')
                self.assertEqual(len(calls), 2)
                if used == 'default':
                    self.assertNotIn('-m', calls[1])
                else:
                    self.assertIn(used, calls[1])
                item = dict(requested=requested, used=used, provider=provider, step='summary')
                self.assertEqual(json.loads((work / 'model_notices.json').read_text()), [item])
                self.assertEqual(json.loads(output.getvalue())['stage'], 'notice')

    def test_non_model_errors_and_tiers_never_retry(self):
        for provider in ('claude', 'codex'):
            for model, diagnostic in (('old', 'permission denied'), ('tier:cheap', 'model_not_found unrecognized_model'), ('tier:default', 'model_not_found unrecognized_model'), ('', 'model_not_found unrecognized_model')):
                error = subprocess.CalledProcessError(1, provider, stderr=diagnostic)
                with patch('summarize.subprocess.run', side_effect=error) as run, patch('tools.codex_cheap_model', return_value='luna'):
                    with self.assertRaises(subprocess.CalledProcessError):
                        summarize.ask_llm(provider, model, 'prompt', Path('/unused'), io.StringIO(), '')
                    self.assertEqual(run.call_count, 1)
        with patch('summarize.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'claude', stderr='unrecognized_model')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                summarize.ask_llm('claude', 'old', 'prompt', Path('/unused'), io.StringIO(), '')
            self.assertEqual(run.call_count, 2)

    def test_sdk_retired_retry(self):
        class StatusError(Exception): pass
        class NotFoundError(StatusError): pass
        class AuthenticationError(StatusError): pass
        class ConnectionError(Exception): pass
        create = Mock(side_effect=[NotFoundError('gone'), NS(stop_reason='end_turn', content=[NS(type='text', text='ok')])])
        module = NS(Anthropic=Mock(return_value=NS(beta=NS(messages=NS(create=create)))),
                    APIStatusError=StatusError, NotFoundError=NotFoundError,
                    AuthenticationError=AuthenticationError, APIConnectionError=ConnectionError)
        notices = []
        with patch.dict('sys.modules', anthropic=module), patch('tools.anthropic_tier_model', return_value='new-haiku'):
            self.assertEqual(summarize.ask_llm('anthropic', 'old-haiku', 'prompt', Path('/unused'), io.StringIO(), '', notice=notices.append), 'ok')
            self.assertEqual([call.kwargs['model'] for call in create.call_args_list], ['old-haiku', 'new-haiku'])
            self.assertEqual(notices[0]['used'], 'new-haiku')
            create.reset_mock(side_effect=True)
            create.side_effect = AuthenticationError('bad key')
            with self.assertRaisesRegex(ValueError, 'key is invalid'):
                summarize.ask_llm('anthropic', 'old-haiku', 'prompt', Path('/unused'), io.StringIO(), '')
            create.assert_called_once()

    def test_organize_persists_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, meetings = root / 'work', root / 'meetings'
            work.mkdir()
            audio = root / 'memo.m4a'
            audio.write_bytes(b'audio')
            for suffix in ('Hinglish.glossary-fixed.txt', 'English.txt', 'for-llm.names-fixed.md'):
                (work / ('memo.' + suffix)).write_text('transcript')
            notices = [dict(requested='retired', used='opus', provider='claude', step='correction')]
            (work / 'model_notices.json').write_text(json.dumps(notices))
            with patch('summarize.audio_duration', return_value=10):
                folder, _ = summarize.organize(audio, work, meetings, datetime.now(),
                                              '# Meeting', 1, 'claude', 'retired', io.StringIO())
            self.assertEqual(json.loads((folder / 'meta.json').read_text())['model_notices'], notices)

    def test_live_notice_committed_by_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = live.CleanupWorker(Path(tmp), dict(provider='claude', model='old'), io.StringIO())
            path = Path(tmp) / 'seg-0000.json'
            item = dict(requested='old', used='opus', provider='claude', step='live_cleanup')
            worker.pending.append((path, [], [], float('inf')))
            worker.results.put((path, '', RuntimeError('bad reply'), 0, [item]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                worker.drain()
            self.assertEqual(json.loads(output.getvalue()), dict(event='model_notice', **item))
            self.assertEqual(json.loads((Path(tmp) / 'cleanup_stats.json').read_text())['model_notices'], [item])
