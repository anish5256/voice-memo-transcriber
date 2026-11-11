import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import clean
import live_transcribe as live
import summarize
import numpy as np


def row(text='meeting hai', **fields):
    return dict(start=0., end=1., hinglish=text, english='meeting', **fields)


class CleanupTests(unittest.TestCase):
    def test_flag_rules_and_boundaries(self):
        cases = [({}, False), ({'avg_logprob': -.7}, False),
                 ({'avg_logprob': -.8}, False), ({'avg_logprob': -.85}, True),
                 ({'compression_ratio': 2.}, False),
                 ({'compression_ratio': 2.01}, True), ({'no_speech_prob': 1.}, False),
                 ({'avg_logprob': None, 'compression_ratio': None}, False)]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(live.flag_lines([row(**fields)]), [0] if expected else [])
        for text, expected in [('We should discuss this later', True), ('go GO, go!', True),
                               ('ab ab', False), ('abcd abcd abcd hai', False),
                               ('we should discuss this hai', False)]:
            self.assertEqual(live.flag_lines([row(text)]), [0] if expected else [])

    def test_prompt_compact_names_context_and_targets(self):
        lines = [row('pehle baat hai'), row('galat naam hai', avg_logprob=-1),
                 row('unrelated baat hai'), row('last baat hai')]
        glossary = '# Instructions\nNever do this rule\n' + '\n'.join(
            f'- Name{i}  (often heard as "WRONG{i}"; private description)' for i in range(50))
        prompt = live.build_cleanup_prompt(lines, [1], glossary)
        self.assertIn('Context: pehle baat hai', prompt)
        self.assertIn('1|galat naam hai\nen: meeting', prompt)
        for absent in ['0|', '2|', '3|', 'unrelated', 'last baat', 'WRONG', 'private',
                       'Instructions', 'Never do this', 'Name40']:
            self.assertNotIn(absent, prompt)
        self.assertIn('Name39', prompt)
        for instruction in ['Plain text only: no Markdown, no ** or other formatting.',
                            'Keep the same script as the input (Roman Hinglish stays Roman; never Devanagari).',
                            'Change only words that are clearly misheard; if unsure, keep the original word.']:
            self.assertIn(instruction, prompt)
        self.assertLess(len(prompt), 1000)
        self.assertNotIn('Context:', live.build_cleanup_prompt(lines, [0, 1]))

    def test_reply_junk_unknown_duplicate_and_length_guard(self):
        lines = [row('a b c     ')] * 5
        reply = ('junk\n```\n99|unknown\n1|not sent\n 0|a b c! \n0|a b c!!\n'
                 '2|a b c!!!!!!!!!!!\n3|a b c\n4|a b c!!!!!!!!!!!!\n```\n')
        self.assertEqual(live.parse_cleanup_reply(reply, lines, [0, 2, 3, 4]),
                         {0: 'a b c!', 2: 'a b c!!!!!!!!!!!'})

    def test_reply_strips_markdown_before_validation(self):
        lines = [row('meeting hai'), row('meeting hai')]
        self.assertEqual(live.parse_cleanup_reply(
            '**0|**MEETING **hai**\n1|__meeting__ __HAI__', lines, [0, 1]),
            {0: 'MEETING hai', 1: 'meeting HAI'})

    def test_reply_devanagari_requires_original_script(self):
        lines = [row('meeting hai'), row('मीटिंग है')]
        self.assertEqual(live.parse_cleanup_reply(
            '0|meeting है\n1|मीटिंग थी', lines, [0, 1]), {1: 'मीटिंग थी'})

    def test_reply_word_overlap_and_normal_fixes(self):
        cases = [('Assosiatied wa hai', 'Associated with billet', False),
                 ('Assosiatied wa hai', 'Associated with the billet', False),
                 ('Assosiatied wa hai', 'Associated wa hai', True),
                 ('yeh galat naam hai', 'YEH, sahi name HAI!', True),
                 ('yeh galat naam hai', 'yeh sahi name tha', False)]
        for original, correction, accepted in cases:
            with self.subTest(correction=correction):
                self.assertEqual(live.parse_cleanup_reply('0|' + correction, [row(original)], [0]),
                                 {0: correction} if accepted else {})

    def worker(self, root):
        root = Path(root)
        (root / 'segments').mkdir(exist_ok=True)
        return live.CleanupWorker(root, dict(provider='claude', model='small'), io.StringIO())

    def submit(self, worker, index):
        path = worker.session / 'segments' / f'seg-{index:05d}.json'
        lines = [row(avg_logprob=-1), row('theek hai')]
        path.write_text(json.dumps(lines))
        worker.submit(path, lines)
        return path

    def test_background_order_stats_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.worker(tmp)
            first, second = threading.Event(), threading.Event()
            calls = []
            def ask(provider, model, prompt, work, log, url, **kwargs):
                calls.append((provider, model, prompt))
                if len(calls) == 1:
                    first.wait(2)
                else:
                    second.set()
                return '0|meeting HAI'
            output = io.StringIO()
            with patch('summarize.ask_llm', side_effect=ask), contextlib.redirect_stdout(output):
                p1 = self.submit(worker, 1)
                # Ensure call 1 has started before submitting call 2.
                deadline = time.monotonic() + 2
                while not calls and time.monotonic() < deadline:
                    time.sleep(.001)
                p2 = self.submit(worker, 2)
                self.assertTrue(second.wait(2))
                worker.drain()
                self.assertNotIn('hing_clean', json.loads(p2.read_text())[0])
                first.set()
                worker.finish()
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([e['index'] for e in events], [1, 2])
            self.assertEqual(events[0]['text'], 'meeting HAI theek hai')
            self.assertEqual(json.loads(p1.read_text())[0]['hing_clean'], 'meeting HAI')
            stats = json.loads((Path(tmp) / 'cleanup_stats.json').read_text())
            self.assertEqual([stats[k] for k in ('segments', 'lines_total', 'lines_sent', 'calls')], [2, 4, 2, 2])
            self.assertEqual(stats['chars_sent'], sum(len(call[2]) for call in calls))
            self.assertEqual(stats['chars_received'], 2 * len('0|meeting HAI'))
            self.assertEqual(stats['approx_tokens'], (stats['chars_sent'] + stats['chars_received']) / 4)
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])

    def test_failure_timeout_and_late_reply_keep_originals(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as tmp:
                worker = self.worker(tmp)
                release = threading.Event()
                def ask(*args, **kwargs):
                    if timeout:
                        release.wait(2)
                        return '0|meeting HAI'
                    raise RuntimeError('test failure')
                with patch('summarize.ask_llm', side_effect=ask), patch.object(live, 'CLEANUP_TIMEOUT', .02):
                    path = self.submit(worker, 1)
                    original = path.read_bytes()
                    worker.finish()
                    self.assertEqual(path.read_bytes(), original)
                    release.set()
                    time.sleep(.03)
                    worker.drain()
                    self.assertEqual(path.read_bytes(), original)
                self.assertIn('keeping originals', worker.log.getvalue())
                self.assertEqual(worker.stats['chars_received'], 0)

    def test_final_correction_excludes_cleaned_and_preserves_text(self):
        bundle = ('## Glossary\n- Name\n\n## Segments\n\n' + '\n\n'.join(
            f'[{i}:00.0] Speaker 1\n  Hinglish: {text}\n  English:  hint {i}'
            for i, text in enumerate(['live cleaned hai', 'needs fixing hai'])))
        with patch('summarize.ask_llm', return_value='[1:00.0] Speaker 1: now repaired hai') as ask, contextlib.redirect_stdout(io.StringIO()):
            result, transcript, counts = summarize.correct_bundle(bundle, 'fake', '', Path('/work'), io.StringIO(), '', [0])
        self.assertNotIn('live cleaned hai', ask.call_args.args[2])
        self.assertNotIn('hint 0', ask.call_args.args[2])
        self.assertIn('Hinglish: live cleaned hai', result)
        self.assertIn('[0:00.0] Speaker 1: live cleaned hai', transcript)
        self.assertEqual(counts, dict(accepted=1, rejected=0, missing=0))
        with patch('summarize.ask_llm') as ask:
            self.assertEqual(summarize.correct_bundle(bundle, 'fake', '', Path('/work'), io.StringIO(), '', [0, 1])[0], bundle)
            ask.assert_not_called()

    def test_glossary_cleanup_preserves_live_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'recording.for-llm.md'
            bundle.write_text('## Glossary\n- Name  (often heard as "wrong")\n\n## Segments\n\n'
                              '[0:00.0] Speaker 1\n  Hinglish: wrong hai\n  English:  wrong\n')
            (Path(tmp) / 'recording.live-cleaned.json').write_text('[0]')
            with patch('sys.argv', ['clean.py', str(bundle)]), contextlib.redirect_stdout(io.StringIO()):
                clean.main()
            self.assertIn('wrong hai', (Path(tmp) / 'recording.Hinglish.glossary-fixed.txt').read_text())

    def test_metrics_retained_from_whisper(self):
        engine = live.Engine()
        @contextlib.contextmanager
        def audio(path):
            yield np.zeros(live.tr.SR)
        h = dict(start=0, end=1, text='meeting hai', avg_logprob=-.8,
                 no_speech_prob=.2, compression_ratio=2.1)
        with patch.object(engine, 'audio', audio), patch.object(engine, 'segment_mask', return_value=np.ones(10, bool)), patch.object(
                live.tr, 'transcribe_result', return_value=dict(text='meeting hai', segments=[h])):
            result = engine.transcribe_segment(Path('unused'))
        for key in ('avg_logprob', 'no_speech_prob', 'compression_ratio'):
            self.assertEqual(result[0][key], h[key])

    def test_timely_completion_applied_after_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.worker(tmp)
            path = Path(tmp) / 'segments' / 'seg-00001.json'
            lines = [row(avg_logprob=-1)]
            path.write_text(json.dumps(lines))
            worker.pending.append((path, lines, [0], 10.))
            worker.results.put((path, '0|meeting HAI', None, 9.))
            with patch.object(live.time, 'monotonic', return_value=11.), contextlib.redirect_stdout(io.StringIO()):
                worker.drain()
            self.assertEqual(json.loads(path.read_text())[0]['hing_clean'], 'meeting HAI')

    def test_unflagged_segment_no_call_and_restart_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.worker(tmp)
            path = Path(tmp) / 'segments' / 'seg-00001.json'
            lines = [row()]
            with patch('summarize.ask_llm') as ask:
                worker.submit(path, lines)
                worker.submit(path, lines)
                self.assertEqual(worker.stats['segments'], 1)
                self.assertEqual(worker.stats['lines_total'], 1)
                self.assertEqual(worker.stats['calls'], 0)
                restarted = self.worker(tmp)
                restarted.submit(path, lines)
                self.assertEqual(restarted.stats['segments'], 1)
                self.assertEqual(restarted.stats['lines_total'], 1)
                ask.assert_not_called()
