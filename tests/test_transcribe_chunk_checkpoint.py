import os
import tempfile
import unittest

from mfaren import transcribe_chunk_checkpoint as tcheck


class TestTranscribeChunkCheckpoint(unittest.TestCase):
    def test_checkpoint_roundtrip_and_done_collection(self):
        chunks_plan = [
            {"index": 0, "start": 0.0, "duration": 10.0, "non_overlap_start": 0.0},
            {"index": 1, "start": 9.0, "duration": 10.0, "non_overlap_start": 10.0},
        ]
        cp = tcheck.new_checkpoint({"backend": "faster_whisper"}, chunks_plan)
        tcheck.mark_chunk_done(cp, 0, [(0.0, 1.0, "ola")])
        tcheck.mark_chunk_failed(cp, 1, "erro teste")

        with tempfile.TemporaryDirectory(prefix="mfaren_test_cp_") as tmpdir:
            path = os.path.join(tmpdir, "checkpoint_chunks.json")
            tcheck.save_checkpoint(path, cp)
            loaded = tcheck.load_checkpoint(path)

        self.assertIsInstance(loaded, dict)
        done = tcheck.collect_done_chunks(loaded)
        missing = tcheck.missing_chunk_indexes(loaded)
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["index"], 0)
        self.assertEqual(done[0]["segments"][0][2], "ola")
        self.assertEqual(missing, [1])

    def test_normalize_checkpoint_preserves_done_when_plan_matches(self):
        plan = [
            {"index": 0, "start": 0.0, "duration": 20.0, "non_overlap_start": 0.0},
            {"index": 1, "start": 18.0, "duration": 20.0, "non_overlap_start": 20.0},
        ]
        existing = tcheck.new_checkpoint({"model": "large-v3"}, plan)
        tcheck.mark_chunk_done(existing, 1, [(0.5, 1.5, "texto")])
        out = tcheck.normalize_checkpoint(existing, {"model": "large-v3"}, plan, keep_done=True)

        done = tcheck.collect_done_chunks(out)
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["index"], 1)
        self.assertEqual(done[0]["segments"][0][2], "texto")


if __name__ == "__main__":
    unittest.main()
