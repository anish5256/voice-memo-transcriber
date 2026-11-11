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
