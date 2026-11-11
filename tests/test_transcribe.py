import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
import transcribe as tr


class DecodeTests(unittest.TestCase):
    def decode(self, segments, mode='hing', prompt=None, seconds=1):
        result = dict(text=' '.join(s['text'] for s in segments), segments=segments)
        decoder = Mock(return_value=result)
        with patch.dict('sys.modules', {'mlx_whisper': SimpleNamespace(transcribe=decoder)}):
            got = tr.transcribe_result(np.zeros(int(seconds * tr.SR)), mode, prompt)
            piece = tr.transcribe_piece(np.zeros(int(seconds * tr.SR)), mode, prompt)
        self.assertTrue(all(isinstance(call.args[0], np.ndarray) for call in decoder.call_args_list))
        self.assertEqual(piece, got['text'].strip())
        return got

    def test_echo_and_real_speech(self):
        echo = dict(start=0, end=.3, text='Aapka CHALO, phir baad mein baat karte hain.')
        real = dict(start=.3, end=1, text='Theek hai, invoice aaj accounts team ko bhej dena.')
        got = self.decode([echo, real])
        self.assertEqual(got['segments'], [real])
        self.assertEqual(got['text'], real['text'])
        self.assertEqual(self.decode([echo])['text'], '')
        self.assertEqual(self.decode([real])['text'], real['text'])
        short = dict(start=0, end=1, text='theek hai')
        self.assertEqual(self.decode([short])['segments'], [short])
        self.assertEqual(self.decode([echo], mode='en')['segments'], [echo])

    def test_silence_requires_both_fields_and_strict_thresholds(self):
        for mode in ('hing', 'en'):
            for fields, dropped in [({'no_speech_prob': .7, 'avg_logprob': -1.1}, True),
                                    ({'no_speech_prob': .6, 'avg_logprob': -1.1}, False),
                                    ({'no_speech_prob': .7, 'avg_logprob': -1.0}, False),
                                    ({'no_speech_prob': .7}, False),
                                    ({'avg_logprob': -1.1}, False), ({}, False)]:
                with self.subTest(mode=mode, fields=fields):
                    segment = dict(start=0, end=1, text='Invoice bhej dena', **fields)
                    self.assertEqual(self.decode([segment], mode)['segments'], [] if dropped else [segment])

    def test_custom_prompt_actually_used(self):
        custom = 'Copper casting quality inspection tomorrow morning.'
        echo = dict(start=0, end=1, text='QUALITY inspection, tomorrow morning!')
        self.assertEqual(self.decode([echo], prompt=custom)['text'], '')
        self.assertEqual(self.decode([echo])['segments'], [echo])
        default_echo = dict(start=0, end=1, text='chalo phir baad mein')
        self.assertEqual(self.decode([default_echo], prompt=custom)['segments'], [default_echo])


    def fallback(self, primary, hindi, seconds=10):
        calls = []
        def decoder(clip, **kw):
            calls.append(kw)
            text = hindi if kw['language'] == 'hi' else primary
            return dict(text=text, segments=[dict(start=0, end=seconds, text=text)] if text else [])
        with patch.dict('sys.modules', {'mlx_whisper': SimpleNamespace(transcribe=decoder)}):
            got = tr.transcribe_result(np.zeros(int(seconds * tr.SR)), 'hing', None)
        return got['text'].strip(), calls

    def test_hindi_fallback_when_roman_decode_is_empty_or_sparse(self):
        hindi = 'यह invoice कल accounts team को भेज दो'
        roman = 'yah invoice kal accounts team ko bhej do'
        # prompt echo only -> filtered to nothing -> Hindi decode, romanized, no prompt
        text, calls = self.fallback('Aapka chalo phir baad mein baat karte hain.', hindi)
        self.assertEqual(text, roman)
        self.assertEqual(calls[-1]['language'], 'hi')
        self.assertNotIn('initial_prompt', calls[-1])
        # one short sentence for a 10 s clip -> the fuller Hindi decode wins
        self.assertEqual(self.fallback('Theek hai.', hindi)[0], roman)
        # a normal decode is kept and costs no second pass
        full = 'Theek hai, invoice aaj accounts team ko bhej dena.'
        text, calls = self.fallback(full, hindi)
        self.assertEqual((text, len(calls)), (full, 1))
        # short clips are never treated as sparse; a worse fallback never replaces the original
        self.assertEqual(self.fallback('Haan.', hindi, seconds=3)[0], 'Haan.')
        self.assertEqual(self.fallback('Theek hai.', '')[0], 'Theek hai.')
        self.assertEqual(self.fallback('Theek hai.', 'लुट ' * 30)[0], 'Theek hai.')

    def test_file_paths_rejected(self):
        with self.assertRaisesRegex(TypeError, 'numpy array'):
            tr.transcribe_result('recording.wav', 'en', None)
