import unittest
import tempfile
import os

from mfaren import transcribe_recovery as trecov


class TestTranscribeRecovery(unittest.TestCase):
    def test_repetition_detection(self):
        live_tail = [
            "[00:00:00 --> 00:00:02] Falante 1 — teste repetido",
            "[00:00:02 --> 00:00:04] Falante 1 — teste repetido",
            "[00:00:04 --> 00:00:06] Falante 1 — teste repetido",
            "[00:00:06 --> 00:00:08] Falante 1 — teste repetido",
            "[00:00:08 --> 00:00:10] Falante 1 — teste repetido",
            "[00:00:10 --> 00:00:12] Falante 1 — teste repetido",
            "[00:00:12 --> 00:00:14] Falante 1 — teste repetido",
            "[00:00:14 --> 00:00:16] Falante 1 — teste repetido",
            "[00:00:16 --> 00:00:18] Falante 1 — teste repetido",
            "[00:00:18 --> 00:00:20] Falante 1 — teste repetido",
            "[00:00:20 --> 00:00:22] Falante 1 — teste repetido",
            "[00:00:22 --> 00:00:24] Falante 1 — teste repetido",
        ]
        result = trecov.detect_repetition(live_tail, [], [])
        self.assertTrue(result["suspected"])
        self.assertGreaterEqual(result["top_ratio"], 0.45)

    def test_retry_patch_is_safe(self):
        opts = {
            "chunk_seconds": "600",
            "chunk_overlap_seconds": "1.5",
            "beam_size": "5",
            "max_len": "42",
        }
        patch = trecov.build_retry_patch(opts, "audio.wav", "loop")
        self.assertEqual(patch["redo_from"], "transcribe")
        self.assertEqual(patch["chunk_seconds"], "180.0")
        self.assertEqual(patch["chunk_overlap_seconds"], "3.0")
        self.assertEqual(patch["beam_size"], "3")
        self.assertEqual(patch["max_len"], "32")

    def test_infer_chunk_from_app_log(self):
        with tempfile.TemporaryDirectory(prefix="mfaren_test_log_resume_") as tmpdir:
            log_path = os.path.join(tmpdir, "app.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("2026-02-14 01:00:00 [INFO] jobs: transcribe_file_start input=C:\\tmp\\audio.flac\n")
                f.write("2026-02-14 01:00:01 [INFO] jobs: chunk_done index=1/49 segments=40\n")
                f.write("2026-02-14 01:00:02 [INFO] jobs: chunk_done index=2/49 segments=32\n")
                f.write("2026-02-14 01:00:03 [ERROR] jobs: chunk_failed index=3/49 error=loop detectado\n")
            out = trecov.infer_chunk_from_app_log("C:\\tmp\\audio.flac", log_path=log_path)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("total_chunks"), 49)
            self.assertEqual(out.get("last_done_chunk_index"), 1)
            self.assertEqual(out.get("failed_chunk_index"), 2)
            self.assertEqual(out.get("suggested_chunk_index"), 2)


if __name__ == "__main__":
    unittest.main()
