import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import live_transcribe as live
import summarize


class FakeEngine:
    def __init__(self):
        self.transcribed, self.embedded = [], []

    def transcribe_segment(self, path):
        self.transcribed.append(path.name)
        return [dict(start=.1, end=.8, hinglish='Haan meeting hai', english='There is a meeting')]

    def embed_segment(self, path):
        self.embedded.append(path.name)
        return dict(windows=np.array([[0., 1.]]),
                    embeddings0=np.array([[1., 0.]]), embeddings1=np.array([[1., 0.]]))


class LiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.session = self.root / 'session'
        (self.session / 'segments').mkdir(parents=True)
        (self.session / 'session.json').write_text(json.dumps(
            dict(started_at='2026-05-07T12:30:00+05:30', segment_seconds=30)))
        self.engine = FakeEngine()
        patcher = patch.object(live, 'ENGINE', self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = io.StringIO()

    def segment(self, index, done=True, real=False):
        path = self.session / 'segments' / f'seg-{index:05d}.m4a'
        if real:
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=1', '-c:a', 'aac', str(path)],
                           check=True, capture_output=True)
        else:
            path.touch()
        if done:
            path.with_suffix('.done').touch()
        return path

    def enable_system(self):
        meta = self.session / 'session.json'
        meta.write_text(json.dumps(dict(started_at='2026-05-07T12:30:00+05:30',
                                        system_audio=True)))

    def tone(self, path, volume):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1:sample_rate=48000',
                        '-af', f'volume={volume}', '-c:a', 'aac', str(path)],
                       check=True, capture_output=True)

    def test_source_rule(self):
        tone = np.ones(live.tr.SR) * .1
        silence = np.zeros(live.tr.SR)
        self.assertEqual(live.source_for(tone * .2, tone, 0, 1), 'sys')
        self.assertEqual(live.source_for(tone, silence, 0, 1), 'mic')
        self.assertEqual(live.source_for(tone * 4, tone, 0, 1), 'mic')
        self.assertIsNone(live.source_for(silence, silence, 0, 1))
        self.assertAlmostEqual(live.rms_db(tone, 0, 1), -20.)

    def test_system_mix_remote_clusters_and_recording(self):
        self.enable_system()
        paths = [self.segment(i) for i in range(1, 4)]
        for i, path in enumerate(paths):
            self.tone(path, 0 if i != 1 else 1)
            self.tone(path.with_suffix('.sys.m4a'), 1 if i != 1 else 0)
        def embed(path):
            self.engine.embedded.append(path.name)
            vector = [0., 1.] if '00003' in path.name else [1., 0.]
            return dict(windows=np.array([[0., 1.]] * 4),
                        embeddings0=np.array([vector] * 4),
                        embeddings1=np.array([vector] * 4))
        work = self.root / 'work'
        with patch.object(self.engine, 'embed_segment', side_effect=embed), patch.object(
                live.tr, 'spectral_cluster', return_value=(np.array([0]*8 + [1]*4), 2)), patch.object(
                live.tr, 'separation', return_value=1):
            audio, count = live.finalize(self.session, work, self.log)
        self.assertEqual(count, 3)
        self.assertEqual([r[2] for r in json.loads((work / 'pieces.json').read_text())], [2, 1, 3])
        self.assertTrue(all(name.endswith('.sys.m4a') for name in self.engine.embedded))
        self.assertTrue(all(name.endswith('.mix.m4a') for name in self.engine.transcribed))
        self.assertEqual([json.loads(p.with_suffix('.json').read_text())[0]['source']
                          for p in paths], ['sys', 'mic', 'sys'])
        bundle = (work / 'recording.for-llm.md').read_text()
        self.assertIn('## Speakers\n\n' + live.SYSTEM_NOTE, bundle)
        self.assertLess(bundle.index('## Speakers'), bundle.index('## Segments'))
        import clean
        with patch('sys.argv', ['clean.py', str(work / 'recording.for-llm.md')]):
            clean.main()
        fixed = (work / 'recording.for-llm.names-fixed.md').read_text()
        self.assertEqual(bundle.split('## Segments')[0], fixed.split('## Segments')[0])
        self.assertTrue(all('.mix.m4a' in line for line in
                            (self.session / 'concat.txt').read_text().splitlines()))
        self.assertGreater(live.duration(audio), 2.9)
        x = live.decode_track(audio)
        self.assertGreater(np.sqrt(np.mean(x[:live.tr.SR] ** 2)), .05)

    def test_single_remote_and_missing_system(self):
        self.enable_system()
        a, b = self.segment(1), self.segment(2)
        self.tone(a, .1)  # Echo: microphone is weaker than the call.
        self.tone(a.with_suffix('.sys.m4a'), 1)
        self.tone(b, 1)
        work = self.root / 'work'
        live.finalize(self.session, work, self.log)
        self.assertEqual([r[2] for r in json.loads((work / 'pieces.json').read_text())], [2, 1])
        self.assertIn(b.name, self.engine.transcribed)
        self.assertIn('missing/unreadable', self.log.getvalue())

    def test_enabled_without_system_is_unchanged(self):
        self.enable_system()
        a = self.segment(1, real=True)
        work = self.root / 'work'
        live.finalize(self.session, work, self.log)
        self.assertEqual(self.engine.transcribed, [a.name])
        self.assertEqual(self.engine.embedded, [a.name])
        self.assertNotIn('source', json.loads(a.with_suffix('.json').read_text())[0])
        self.assertNotIn(live.SYSTEM_NOTE, (work / 'recording.for-llm.md').read_text())

    def test_call_assignment_single_cluster_and_absent_tracks(self):
        rows = [dict(start=i, end=i+.5, source=source, hinglish='h', english='e')
                for i, source in enumerate(['sys', None, 'sys', 'mic'])]
        pieces = live.assign_call_speakers(np.array([[0., 1.], [2., 3.]]),
                                          np.array([0, 0]), rows)
        self.assertEqual([p[2] for p in pieces], [2, 2, 2, 1])

    def test_unreadable_system_is_mic_only(self):
        self.enable_system()
        path = self.segment(1, real=True)
        path.with_suffix('.sys.m4a').write_bytes(b'broken')
        work = self.root / 'work'
        live.finalize(self.session, work, self.log)
        self.assertEqual(self.engine.transcribed, [path.name])
        self.assertEqual(self.engine.embedded, [path.name])
        self.assertIn('missing/unreadable', self.log.getvalue())
        self.assertNotIn(live.SYSTEM_NOTE, (work / 'recording.for-llm.md').read_text())

    def test_discovery(self):
        b, a = self.segment(10), self.segment(2)
        self.segment(1, False)
        (a.parent / 'seg-no.done').touch()
        self.assertEqual(live.discover(self.session), [a, b])

    def test_atomic_idempotence_and_stop(self):
        path = self.segment(1)
        (self.session / 'STOP').touch()
        out = io.StringIO()
        with patch.object(live, 'duration', return_value=2), contextlib.redirect_stdout(out):
            self.assertEqual(live.watch(self.session, log=self.log), 0)
            before = (self.session / 'live.txt').read_bytes()
            live.watch(self.session, log=self.log)
        self.assertEqual(self.engine.transcribed, [path.name])
        self.assertEqual(self.engine.embedded, [path.name])
        self.assertEqual((self.session / 'live.txt').read_bytes(), before)
        self.assertEqual(before.decode(), '[0:00] Haan meeting hai\n')
        self.assertEqual([json.loads(x)['event'] for x in out.getvalue().splitlines()],
                         ['segment', 'stopped', 'stopped'])
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_atomic_failure(self):
        target = self.session / 'live.txt'
        target.write_text('old')
        def fail(f):
            f.write(b'partial')
            raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            live.atomic(target, fail)
        self.assertEqual(target.read_text(), 'old')
        self.assertEqual(list(self.session.glob('*.tmp')), [])

    def test_offsets_skip_bad(self):
        a, bad, c = [self.segment(i) for i in (1, 2, 3)]
        def probe(path):
            if path == bad:
                raise ValueError('truncated')
            return 30 if path == a else 2
        with patch.object(live, 'duration', side_effect=probe):
            rows = live.scan(self.session, self.log)
        self.assertEqual([r[1] for r in rows], [0, 30])
        self.assertIn('[0:30]', (self.session / 'live.txt').read_text())
        self.assertIn('Skipping unreadable segment seg-00002', self.log.getvalue())

    def test_finalize_from_scratch(self):
        self.segment(1, real=True)
        work = self.root / 'work'
        audio, speakers = live.finalize(self.session, work, self.log)
        self.assertTrue(audio.is_file())
        self.assertNotIn('## Speakers', (work / 'recording.for-llm.md').read_text())
        self.assertEqual(speakers, 1)
        self.assertIn('[0:00.1] Speaker 1:', (work / 'recording.Hinglish.txt').read_text())
        self.assertIn('English:  There is a meeting', (work / 'recording.for-llm.md').read_text())
        self.assertEqual(json.loads((work / 'pieces.json').read_text()), [[.1, .8, 1]])
        live.finalize(self.session, work, self.log)
        self.assertEqual(len(self.engine.transcribed), 1)

    def test_finalize_after_partial_watch(self):
        a = self.segment(1, real=True)
        live.scan(self.session, self.log)
        self.segment(2, real=True)
        # Crash between NPZ and JSON commit: retain the finished embedding half.
        a.with_suffix('.json').unlink()
        live.finalize(self.session, self.root / 'work', self.log)
        self.assertEqual(self.engine.embedded.count(a.name), 1)
        self.assertEqual(len(self.engine.transcribed), 3)

    def test_assignment_overlap_first_appearance_and_ties(self):
        windows = np.array([[0, 1], [.5, 1.5], [1, 2], [1.5, 2.5]])
        labels = np.array([9, 9, 2, 2])
        rows = [dict(start=a, end=b, hinglish='h', english='e')
                for a, b in [(0, .5), (1.6, 2.2), (.75, 1.75)]]
        self.assertEqual([r[2] for r in live.assign_speakers(windows, labels, rows)], [1, 2, 1])

    def test_whisper_modes_timestamps_and_retries(self):
        from types import SimpleNamespace
        calls = []
        def decode(clip, **kw):
            calls.append(kw)
            if kw['task'] == 'translate':
                return dict(text='one two', segments=[
                    dict(start=0, end=.4, text='one'),
                    dict(start=.4, end=1, text='two')])
            return dict(text='haan', segments=[dict(start=.2, end=.8, text='haan')])
        engine = live.Engine(prompt='custom')
        @contextlib.contextmanager
        def audio(path):
            yield np.zeros(32000)
        with patch.object(engine, 'audio', audio), patch.object(
                engine, 'segment_mask', return_value=np.ones(20, bool)), patch.dict(
                'sys.modules', {'mlx_whisper': SimpleNamespace(transcribe=decode)}):
            rows = engine.transcribe_segment(Path('unused'))
        self.assertEqual(rows, [dict(start=.2, end=.8, hinglish='haan', english='one two')])
        self.assertEqual((calls[0]['language'], calls[0]['initial_prompt']), ('en', 'custom'))
        self.assertEqual((calls[1]['language'], calls[1]['task']), ('hi', 'translate'))
        from unittest.mock import Mock
        decoder = Mock(return_value=dict(text='go go go go go go go go', segments=[]))
        with patch.dict('sys.modules', {'mlx_whisper': SimpleNamespace(transcribe=decoder)}):
            result = live.tr.transcribe_result(np.zeros(32000), 'hing', None)
        self.assertEqual(decoder.call_count, 3)
        self.assertEqual([c.kwargs['temperature'] for c in decoder.call_args_list],
                         [(0., .2, .4, .6), (.4,), (.8,)])
        self.assertIn('unclear', result['segments'][0]['text'])

    def test_live_filters_echo_and_silence_before_rows(self):
        from types import SimpleNamespace
        engine = live.Engine()
        real = dict(start=.5, end=1, text='theek hai')
        def decode(clip, **kw):
            if kw['task'] == 'translate':
                segments = [dict(start=0, end=.5, text='invented',
                                 no_speech_prob=.9, avg_logprob=-2),
                            dict(start=.5, end=1, text='all right')]
            else:
                segments = [dict(start=0, end=.5, text='chalo phir baad mein'), real]
            return dict(text=' '.join(s['text'] for s in segments), segments=segments)
        @contextlib.contextmanager
        def audio(path):
            yield np.zeros(live.tr.SR)
        with patch.object(engine, 'audio', audio), patch.object(
                engine, 'segment_mask', return_value=np.ones(10, bool)), patch.dict(
                'sys.modules', {'mlx_whisper': SimpleNamespace(transcribe=decode)}):
            rows = engine.transcribe_segment(Path('unused'))
        self.assertEqual(rows, [dict(start=.5, end=1, hinglish='theek hai', english='all right')])

    def test_piece_splitting(self):
        self.assertEqual(live.speech_pieces(np.ones(300, bool), 30),
                         [(0, 15), (15, 30)])
        mask = np.ones(300, bool)
        mask[90:100] = False
        mask[210:230] = False
        self.assertEqual(live.speech_pieces(mask, 30),
                         [(0, 9), (10, 21), (23, 30)])
        mask = np.ones(300, bool)
        mask[50:60] = False
        mask[130:150] = False
        self.assertEqual(live.speech_pieces(mask, 30), [(0, 13), (15, 30)])
        mask = np.ones(300, bool)
        mask[100:102] = False  # Less than 300 ms does not qualify as a cut.
        self.assertEqual(live.speech_pieces(mask, 30), [(0, 15), (15, 30)])
        # A short trailing utterance merges into its neighbour and is recut.
        mask = np.ones(180, bool)
        mask[140:160] = False
        pieces = live.speech_pieces(mask, 18)
        self.assertEqual(pieces, [(0, 14), (14, 18)])
        self.assertTrue(all(4 <= b-a <= 15 for a, b in pieces))
        self.assertEqual(live.speech_pieces(np.zeros(302, bool), 30), [])
        mask = np.zeros(302, bool)
        mask[50:100] = True
        self.assertEqual(live.speech_pieces(mask, 30), [(5, 10)])
        self.assertEqual(live.speech_pieces(np.ones(170, bool), 17),
                         [(0, 13), (13, 17)])

    def test_english_detector(self):
        self.assertFalse(live.looks_english('Only four words here'))
        self.assertTrue(live.looks_english('Okay, we should discuss this tomorrow.'))
        self.assertFalse(live.looks_english('Okay, we should discuss this kal HAI.'))
        self.assertTrue(live.looks_english('There are many different themes today'))
        for word in live.HINDI_WORDS:
            self.assertFalse(live.looks_english('Okay we should discuss this ' + word))

    def test_piece_timestamps_padding_and_mask_reuse(self):
        from types import SimpleNamespace
        engine = live.Engine()
        engine.embedders = [lambda x, a, b: np.array([1., 0.])] * 2
        mask = np.zeros(302, bool)
        mask[50:100] = True
        mask[200:250] = True
        lengths = []
        def decode(clip, **kw):
            lengths.append(len(clip) / live.tr.SR)
            return dict(text='meeting hai', segments=[
                dict(start=.2, end=1, text='meeting hai')])
        @contextlib.contextmanager
        def audio(path):
            yield np.zeros(30 * live.tr.SR)
        with patch.object(engine, 'audio', audio), patch.object(
                live.tr, 'segmentation_model', return_value=object()), patch.object(
                live.tr, 'speech_mask', return_value=mask) as detect, patch.dict(
                'sys.modules', {'mlx_whisper': SimpleNamespace(transcribe=decode)}):
            engine.embed_segment(Path('unused'))
            rows = engine.transcribe_segment(Path('unused'))
        self.assertEqual(detect.call_count, 1)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]['start'], 5)
        self.assertAlmostEqual(rows[0]['end'], 5.8)
        self.assertAlmostEqual(rows[1]['start'], 20)
        self.assertAlmostEqual(rows[1]['end'], 20.8)
        np.testing.assert_allclose(lengths, [5.4] * 4)

    def test_silence_never_decoded(self):
        engine = live.Engine()
        @contextlib.contextmanager
        def audio(path):
            yield np.zeros(30 * live.tr.SR)
        with patch.object(engine, 'audio', audio), patch.object(
                engine, 'segment_mask', return_value=np.zeros(302, bool)), patch.object(
                live.tr, 'transcribe_result') as decode:
            self.assertEqual(engine.transcribe_segment(Path('unused')), [])
        decode.assert_not_called()

    def test_single_script_retry(self):
        from types import SimpleNamespace
        original = 'Okay we should discuss this tomorrow'
        for retry_text, expected in [('Haan ye kal discuss karna hai', 'Haan ye kal discuss karna hai'),
                                     ('We will discuss everything next week', original)]:
            with self.subTest(retry=retry_text):
                calls = []
                def decode(clip, **kw):
                    calls.append(kw)
                    text = (original if len(calls) == 1 else retry_text)
                    if kw['task'] == 'translate':
                        text = 'English translation'
                    return dict(text=text, segments=[dict(start=0, end=1, text=text)])
                engine = live.Engine(prompt='custom')
                @contextlib.contextmanager
                def audio(path):
                    yield np.zeros(5 * live.tr.SR)
                with patch.object(engine, 'audio', audio), patch.object(
                        engine, 'segment_mask', return_value=np.ones(50, bool)), patch.dict(
                        'sys.modules', {'mlx_whisper': SimpleNamespace(transcribe=decode)}):
                    rows = engine.transcribe_segment(Path('unused'))
                self.assertEqual(rows[0]['hinglish'], expected)
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[1]['temperature'], .2)
                self.assertEqual(calls[1]['initial_prompt'], live.tr.DEFAULT_HINGLISH_PROMPT +
                    ' Ye transcript Roman Hindi (Hinglish) mein likha jaata hai, English translation nahi.')
                self.assertEqual(calls[2]['task'], 'translate')

    def test_finalize_inference_failure_retains_session(self):
        self.segment(1)
        with patch.object(live, 'duration', return_value=2), patch.object(
                self.engine, 'transcribe_segment', side_effect=RuntimeError('model failed')):
            with self.assertRaisesRegex(RuntimeError, 'model failed'):
                live.finalize(self.session, self.root / 'work', self.log)
        self.assertTrue(self.session.exists())

    def test_zero_segments(self):
        with self.assertRaisesRegex(ValueError, 'No readable'):
            live.finalize(self.session, self.root / 'work', self.log)

    def test_summary_live_complete_and_deletes_session(self):
        self.segment(1, real=True)
        (self.session / 'STOP').touch()
        output = io.StringIO()
        with patch('sys.argv', ['summarize.py', '--live-session', str(self.session),
                                '--meetings-dir', str(self.root / 'Meetings'),
                                '--summary-backend', 'fake']), contextlib.redirect_stdout(output):
            self.assertEqual(summarize.main(), 0, output.getvalue())
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e['stage'] for e in events],
                         ['finalizing', 'cleaning', 'summarizing', 'organizing', 'done'])
        folder = Path(events[-1]['folder'])
        self.assertEqual({p.name for p in folder.iterdir()},
                         {'audio.m4a', 'summary.md', 'transcript.hinglish.txt',
                          'transcript.english.txt', 'transcript.for-llm.md', 'meta.json'})
        self.assertFalse(self.session.exists())
        meta = json.loads((folder / 'meta.json').read_text())
        self.assertEqual(meta['recorded_at'], '2026-05-07T12:30:00+05:30')
        self.assertEqual(meta['speakers'], 1)
        self.assertIn('Haan meeting hai', (folder / 'transcript.hinglish.txt').read_text())


if __name__ == '__main__':
    unittest.main()
