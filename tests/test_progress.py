import unittest

from mfaren.progress import parse_ffmpeg_progress, parse_ytdlp_progress
from mfaren.ffmpeg import normalize_ffmpeg_progress


class TestProgress(unittest.TestCase):
    def test_ytdlp_download_line(self):
        line = "[download]  42.3% of 50.00MiB at 3.20MiB/s ETA 00:12"
        payload = parse_ytdlp_progress(line)
        self.assertIsNotNone(payload)
        self.assertAlmostEqual(payload["percent"], 42.3, places=1)
        self.assertEqual(payload["speed"], "3.20MiB/s")
        self.assertEqual(payload["eta_seconds"], 12)
        self.assertIsNotNone(payload["total_bytes"])

    def test_ytdlp_postprocess_line(self):
        line = "[ffmpeg] Merging formats into \"file.mkv\""
        payload = parse_ytdlp_progress(line)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["message"], "Pós-processamento")

    def test_ffmpeg_progress_parse(self):
        lines = [
            "out_time_ms=5000000",
            "total_size=1048576",
            "speed=1.2x",
            "progress=continue",
        ]
        progress = {}
        for line in lines:
            parsed = parse_ffmpeg_progress(line)
            if parsed:
                progress.update(parsed)
        normalized = normalize_ffmpeg_progress(progress, duration=20)
        self.assertGreaterEqual(normalized["percent"], 24)
        self.assertEqual(normalized["speed"], "1.2x")
        self.assertEqual(normalized["downloaded_bytes"], 1048576)


if __name__ == "__main__":
    unittest.main()
