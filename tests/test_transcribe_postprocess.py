import unittest

from mfaren import transcribe_postprocess as tpost


class TestTranscribePostprocess(unittest.TestCase):
    def test_clean_repetitive_segments_drops_long_short_phrase_loop(self):
        segments = [
            (0.0, 4.0, "intro normal"),
            (4.0, 34.0, "Legenda Adriana Zanotto"),
            (34.0, 64.0, "Legenda Adriana Zanotto"),
            (64.0, 94.0, "Legenda Adriana Zanotto"),
            (94.0, 124.0, "Legenda Adriana Zanotto"),
            (124.0, 129.0, "retoma narrativa"),
        ]
        out = tpost.clean_repetitive_segments(segments)
        texts = [s[2] for s in out]
        self.assertEqual(texts.count("Legenda Adriana Zanotto"), 1)
        self.assertIn("retoma narrativa", texts)


if __name__ == "__main__":
    unittest.main()
