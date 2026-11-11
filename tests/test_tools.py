import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import tools
import summarize
import live_transcribe


class ToolTests(unittest.TestCase):
    def tearDown(self):
        tools.ffmpeg.cache_clear()

    def test_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'binary'
            exe.write_text('test')
            exe.chmod(0o755)
            imageio = SimpleNamespace(get_ffmpeg_exe=Mock(return_value='/bundled/ffmpeg'))
            with patch.dict(os.environ, MEETINGNOTES_FFMPEG=str(exe)), patch.dict(
                    'sys.modules', imageio_ffmpeg=imageio):
                tools.ffmpeg.cache_clear()
                self.assertEqual(tools.ffmpeg(), str(exe))
                exe.chmod(0o644)
                self.assertEqual(tools.ffmpeg(), str(exe))  # cached
                tools.ffmpeg.cache_clear()
                self.assertEqual(tools.ffmpeg(), '/bundled/ffmpeg')
                imageio.get_ffmpeg_exe.assert_called_once_with()
            with patch.dict(os.environ, MEETINGNOTES_FFMPEG=tmp), patch.dict(
                    'sys.modules', imageio_ffmpeg=None):
                tools.ffmpeg.cache_clear()
                self.assertEqual(tools.ffmpeg(), 'ffmpeg')

    def test_audio_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'tone.wav'
            with wave.open(str(wav), 'wb') as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                out.writeframes(b'\0\0' * 16000)
            compressed = Path(tmp) / 'tone.m4a'
            subprocess.run([tools.ffmpeg(), '-v', 'error', '-i', str(wav),
                            str(compressed)], check=True, capture_output=True)
            for path in [wav, compressed]:
                self.assertAlmostEqual(tools.audio_duration(path), 1, delta=.1)
                self.assertTrue(tools.is_readable_audio(path))
            broken = Path(tmp) / 'broken.m4a'
            broken.write_bytes(compressed.read_bytes()[:40])
            for path in [broken, Path(tmp) / 'missing.wav']:
                with self.assertRaisesRegex(ValueError, 'Could not read audio'):
                    tools.audio_duration(path)
                self.assertFalse(tools.is_readable_audio(path))

    def test_pipeline_sources(self):
        root = Path(__file__).resolve().parent.parent
        for name in ['transcribe.py', 'live_transcribe.py', 'summarize.py', 'clean.py',
                     'inbox_worker.py', 'tools.py']:
            source = (root / name).read_text()
            self.assertNotIn('ffprobe', source)
            self.assertNotRegex(source, r'''\[\s*['"]ffmpeg['"]''')

    def test_download_notifications_and_errors(self):
        for cached in [True, False]:
            for live in [True, False]:
                download = Mock(side_effect=['cached'] if cached else [OSError('cache miss'), 'downloaded'])
                output = io.StringIO()
                with patch.dict('sys.modules', huggingface_hub=SimpleNamespace(snapshot_download=download)), contextlib.redirect_stdout(output):
                    tools.ensure_speech_model(lambda: live_transcribe.event('downloading_model') if live else
                        summarize.emit('downloading_model', message=tools.MODEL_MESSAGE))
                self.assertEqual(download.call_count, 1 if cached else 2)
                self.assertEqual(download.call_args_list[0].args, (tools.WHISPER,))
                self.assertEqual(download.call_args_list[0].kwargs, dict(local_files_only=True))
                if cached:
                    self.assertEqual(output.getvalue(), '')
                else:
                    expected = dict(event='downloading_model') if live else dict(
                        stage='downloading_model', message=tools.MODEL_MESSAGE)
                    self.assertEqual(json.loads(output.getvalue()), expected)
                    self.assertEqual(download.call_args_list[1].kwargs, {})
        with patch.dict('sys.modules', huggingface_hub=SimpleNamespace(
                snapshot_download=Mock(side_effect=OSError('offline')))):
            with self.assertRaisesRegex(ValueError, 'Could not download the speech model. Check your internet connection.'):
                tools.ensure_speech_model(lambda: None)

    def test_summary_checks_cache_before_batch_and_live_inference(self):
        for live in [False, True]:
            for cached in [False, True]:
                with self.subTest(live=live, cached=cached), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    audio = root / 'memo.wav'
                    audio.touch()
                    session = root / 'session'
                    session.mkdir()
                    (session / 'session.json').write_text(json.dumps(dict(
                        started_at='2026-11-11T12:30:00+05:30')))
                    download = Mock(side_effect=['cached'] if cached else [OSError('cache miss'), 'downloaded'])
                    def inference(*args, **kwargs):
                        self.assertEqual(download.call_count, 1 if cached else 2)
                        raise RuntimeError('inference reached')
                    argv = ['summarize.py'] + (['--live-session', str(session)] if live else [str(audio)])
                    argv += ['--meetings-dir', str(root / 'Meetings')]
                    output = io.StringIO()
                    with patch.dict('sys.modules', huggingface_hub=SimpleNamespace(snapshot_download=download)), \
                            patch('sys.argv', argv), patch('summarize.run', side_effect=inference), \
                            patch('live_transcribe.finalize', side_effect=inference), contextlib.redirect_stdout(output):
                        self.assertEqual(summarize.main(), 1)
                    events = [json.loads(line) for line in output.getvalue().splitlines()]
                    self.assertEqual([e['stage'] for e in events],
                                     ([] if cached else ['downloading_model']) +
                                     ['finalizing' if live else 'transcribing', 'error'])
                    self.assertEqual(events[-1]['message'], 'inference reached')
