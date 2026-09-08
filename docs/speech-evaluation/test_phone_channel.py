"""Contract checks for the synthetic channel; run in the speech environment."""

import unittest

import numpy as np
from phone_suite import phone_audio, ulaw_roundtrip


class PhoneChannelTests(unittest.TestCase):
    def test_codec_preserves_duration_and_silence(self):
        silence = np.zeros(8000, dtype=np.float32)
        result = ulaw_roundtrip(silence)
        self.assertEqual(result.shape, silence.shape)
        self.assertLess(float(np.max(np.abs(result))), 0.001)

    def test_bandpass_attenuates_low_frequency(self):
        t = np.arange(16000) / 16000
        low = phone_audio(0.2 * np.sin(2 * np.pi * 70 * t), 16000, False, 1)
        voice_band = phone_audio(0.2 * np.sin(2 * np.pi * 1000 * t), 16000, False, 1)
        self.assertEqual(len(voice_band), 8000)
        self.assertLess(float(np.mean(low**2)), float(np.mean(voice_band**2)) / 100)

    def test_seeded_noise_is_repeatable_and_changes_signal(self):
        samples = (0.1 * np.sin(2 * np.pi * 600 * np.arange(16000) / 16000)).astype("float32")
        a = phone_audio(samples, 16000, True, 100)
        b = phone_audio(samples, 16000, True, 100)
        clean = phone_audio(samples, 16000, False, 100)
        self.assertTrue(np.array_equal(a, b))
        self.assertTrue(np.isfinite(a).all())
        self.assertGreater(float(np.mean((a - clean) ** 2)), 0)


if __name__ == "__main__":
    unittest.main()
