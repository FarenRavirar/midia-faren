import unittest

from mfaren.ffmpeg import build_video_cmd


class TestFfmpegVideoCmd(unittest.TestCase):
    def test_strip_audio_enabled_adds_an(self):
        cmd = build_video_cmd(
            "ffmpeg",
            "in.mp4",
            "out.webm",
            {
                "codec": "vp9",
                "resolution": "best",
                "bitrate": "auto",
                "strip_audio": "on",
            },
        )
        self.assertIn("-an", cmd)

    def test_strip_audio_disabled_no_an(self):
        cmd = build_video_cmd(
            "ffmpeg",
            "in.mp4",
            "out.webm",
            {
                "codec": "vp9",
                "resolution": "best",
                "bitrate": "auto",
                "strip_audio": "off",
            },
        )
        self.assertNotIn("-an", cmd)


if __name__ == "__main__":
    unittest.main()

