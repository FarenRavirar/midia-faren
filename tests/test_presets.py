import unittest

from mfaren.presets import validate_options


class TestPresets(unittest.TestCase):
    def test_validate_audio(self):
        options = {"mode": "audio", "format": "mp3", "bitrate": "192", "audio_mode": "cbr"}
        validate_options(options)

    def test_validate_video(self):
        options = {"mode": "video", "container": "mp4", "codec": "h264", "resolution": "720", "bitrate": "auto"}
        validate_options(options)

    def test_custom_bitrate_video(self):
        options = {"mode": "video", "container": "mp4", "codec": "h264", "resolution": "720", "bitrate": "custom", "custom_bitrate": "2500"}
        validate_options(options)

    def test_video_strip_audio(self):
        options = {
            "mode": "video",
            "container": "webm",
            "codec": "vp9",
            "resolution": "best",
            "bitrate": "auto",
            "strip_audio": "on",
        }
        validate_options(options)

    def test_invalid_video_container_codec_combination(self):
        with self.assertRaises(ValueError):
            validate_options(
                {
                    "mode": "video",
                    "container": "webm",
                    "codec": "h264",
                    "resolution": "best",
                    "bitrate": "auto",
                }
            )

    def test_valid_video_container_codec_combination(self):
        validate_options(
            {
                "mode": "video",
                "container": "mp4",
                "codec": "h265",
                "resolution": "best",
                "bitrate": "auto",
            }
        )

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            validate_options({"mode": "invalid"})

    def test_invalid_strip_audio_video(self):
        with self.assertRaises(ValueError):
            validate_options(
                {
                    "mode": "video",
                    "container": "mp4",
                    "codec": "h264",
                    "resolution": "720",
                    "bitrate": "auto",
                    "strip_audio": "talvez",
                }
            )

    def test_validate_transcribe_new_fields(self):
        options = {
            "mode": "transcribe",
            "transcribe_backend": "faster_whisper",
            "chunk_seconds": "300",
            "chunk_overlap_seconds": "2.0",
            "transcribe_device": "cuda",
            "transcribe_compute_type": "float16",
            "whisperx_batch_size": "4",
            "transcribe_initial_prompt": "glossario rpg",
            "transcribe_output_json": "on",
        }
        validate_options(options)

    def test_validate_transcribe_chunk_mm_ss(self):
        options = {
            "mode": "transcribe",
            "transcribe_backend": "faster_whisper",
            "chunk_seconds": "05:00",
            "chunk_overlap_seconds": "2.0",
        }
        validate_options(options)

    def test_validate_transcribe_invalid_backend(self):
        with self.assertRaises(ValueError):
            validate_options({"mode": "transcribe", "transcribe_backend": "unknown"})

    def test_validate_transcribe_invalid_whisperx_batch(self):
        with self.assertRaises(ValueError):
            validate_options(
                {
                    "mode": "transcribe",
                    "transcribe_backend": "whisperx",
                    "whisperx_batch_size": "0",
                }
            )

    def test_validate_transcribe_invalid_output_json(self):
        with self.assertRaises(ValueError):
            validate_options(
                {
                    "mode": "transcribe",
                    "transcribe_backend": "faster_whisper",
                    "transcribe_output_json": "talvez",
                }
            )

    def test_validate_mixagem(self):
        options = {
            "mode": "mixagem",
            "mix_output_format": "m4a",
            "mix_target_bitrate_kbps": "96",
            "mix_max_size_mb": "190",
        }
        validate_options(options)

    def test_validate_mixagem_invalid_format(self):
        with self.assertRaises(ValueError):
            validate_options(
                {
                    "mode": "mixagem",
                    "mix_output_format": "flac",
                    "mix_target_bitrate_kbps": "96",
                    "mix_max_size_mb": "190",
                }
            )


if __name__ == "__main__":
    unittest.main()
