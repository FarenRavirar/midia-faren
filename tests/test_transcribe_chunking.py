import unittest

from mfaren import transcribe_chunking as tchunk


class TestTranscribeChunking(unittest.TestCase):
    def test_parse_chunk_config_accepts_mm_ss(self):
        chunk, overlap = tchunk.parse_chunk_config({"chunk_seconds": "05:00", "chunk_overlap_seconds": "00:02"})
        self.assertAlmostEqual(chunk, 300.0, places=3)
        self.assertAlmostEqual(overlap, 2.0, places=3)

    def test_build_chunks_with_overlap(self):
        chunks = tchunk.build_chunks(620.0, 300.0, 2.0)
        self.assertEqual(len(chunks), 3)
        self.assertAlmostEqual(chunks[0]["start"], 0.0, places=3)
        self.assertAlmostEqual(chunks[1]["start"], 298.0, places=3)
        self.assertAlmostEqual(chunks[1]["non_overlap_start"], 300.0, places=3)

    def test_merge_chunk_segments_deduplicates_overlap(self):
        chunks = [
            {
                "index": 0,
                "start": 0.0,
                "non_overlap_start": 0.0,
                "segments": [(0.0, 1.0, "ola"), (299.2, 300.8, "frase final")],
            },
            {
                "index": 1,
                "start": 298.0,
                "non_overlap_start": 300.0,
                "segments": [(0.5, 2.8, "frase final"), (2.9, 4.1, "proxima")],
            },
        ]
        merged = tchunk.merge_chunk_segments(chunks, source_duration=400.0)
        texts = [seg[2] for seg in merged]
        self.assertEqual(texts.count("frase final"), 1)
        self.assertIn("proxima", texts)


if __name__ == "__main__":
    unittest.main()
