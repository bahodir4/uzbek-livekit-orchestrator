import io
import wave
import unittest
import numpy as np
from voice_agent.audio_utils import split_for_fast_start, split_long_phrase, trim_pcm_edges, decode_wav_pcm16


class TestSayroAudio(unittest.TestCase):
    def test_split_keeps_short(self):
        self.assertEqual(split_long_phrase("Assalomu alaykum!", 70), ["Assalomu alaykum!"])

    def test_fast_start_isolates_short_opener(self):
        result = split_for_fast_start("Xo'p, tushundim. Sizga kredit bo'yicha yordam bera olaman.", 70)
        self.assertEqual(result[0], "Xo'p, tushundim.")
        self.assertEqual(" ".join(result), "Xo'p, tushundim. Sizga kredit bo'yicha yordam bera olaman.")

    def test_fast_start_keeps_short_single_phrase(self):
        self.assertEqual(split_for_fast_start("Salom.", 70), ["Salom."])

    def test_split_long_at_words(self):
        result = split_long_phrase("bir ikki uch to'rt besh olti yetti", 18)
        self.assertTrue(all(len(s) <= 18 for s in result))
        self.assertEqual(" ".join(result), "bir ikki uch to'rt besh olti yetti")

    def test_wav_decoding(self):
        out = io.BytesIO()
        with wave.open(out, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes(b'\0\0'*2400)
        self.assertEqual(len(decode_wav_pcm16(out.getvalue(), 24000)), 4800)
        with self.assertRaises(ValueError):
            decode_wav_pcm16(out.getvalue(), 16000)

    def test_trim_keeps_internal_pause(self):
        tone = np.full(2400, 1400, dtype='<i2')
        silence = np.zeros(12000, dtype='<i2')
        pcm = np.concatenate([silence, tone, silence, tone, silence]).tobytes()
        trimmed = trim_pcm_edges(pcm, threshold=450, sample_rate=24000)
        self.assertLess(len(trimmed), len(pcm))
        self.assertGreater(len(trimmed), len(tone.tobytes())*2 + len(silence.tobytes()))


if __name__ == '__main__':
    unittest.main()
