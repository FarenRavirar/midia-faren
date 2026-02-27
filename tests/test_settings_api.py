import os
import unittest

from mfaren.settings import get_setting, get_settings, set_setting, set_settings


class TestSettingsAPI(unittest.TestCase):
    def setUp(self):
        from app import app

        self.client = app.test_client()
        self.prev_transcribe = get_settings("transcribe")
        self.prev_mixagem = get_settings("mixagem")
        self.prev_craig_notebook = get_settings("craig_notebook")
        self.prev_output_dir = get_setting("output_dir", "")
        self.prev_last_mode = get_setting("last_mode", "")

    def tearDown(self):
        set_settings("transcribe", self.prev_transcribe if isinstance(self.prev_transcribe, dict) else {})
        set_settings("mixagem", self.prev_mixagem if isinstance(self.prev_mixagem, dict) else {})
        set_settings("craig_notebook", self.prev_craig_notebook if isinstance(self.prev_craig_notebook, dict) else {})
        set_setting("output_dir", self.prev_output_dir if self.prev_output_dir is not None else "")
        set_setting("last_mode", self.prev_last_mode if self.prev_last_mode is not None else "")

    def test_transcribe_settings_roundtrip(self):
        output_dir = os.getcwd()
        seed = {
            "mode": "transcribe",
            "data": {
                "transcribe_glossary": "Kovir\nmorrer => Kovir",
                "transcribe_backend": "faster_whisper",
            },
            "output_dir": output_dir,
            "last_mode": "transcribe",
        }
        seed_res = self.client.post("/api/settings", json=seed)
        self.assertEqual(seed_res.status_code, 200)

        payload = {
            "mode": "transcribe",
            "data": {
                "transcribe_backend": "faster_whisper",
                "chunk_seconds": "240",
                "chunk_overlap_seconds": "2.0",
            },
            "output_dir": output_dir,
            "last_mode": "transcribe",
        }
        res = self.client.post("/api/settings", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json() or {}
        self.assertTrue(data.get("ok"))

        get_res = self.client.get("/api/settings?mode=transcribe")
        self.assertEqual(get_res.status_code, 200)
        body = get_res.get_json() or {}
        opts = body.get("data") or {}
        self.assertEqual(opts.get("transcribe_backend"), "faster_whisper")
        self.assertEqual(opts.get("chunk_seconds"), "240")
        self.assertEqual(opts.get("chunk_overlap_seconds"), "2.0")
        self.assertEqual(opts.get("transcribe_glossary"), "Kovir\nmorrer => Kovir")

    def test_mixagem_settings_roundtrip(self):
        payload = {
            "mode": "mixagem",
            "data": {
                "mode": "mixagem",
                "mix_output_format": "m4a",
                "mix_target_bitrate_kbps": "96",
                "mix_max_size_mb": "190",
            },
            "output_dir": os.getcwd(),
            "last_mode": "mixagem",
        }
        res = self.client.post("/api/settings", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertTrue((res.get_json() or {}).get("ok"))

        get_res = self.client.get("/api/settings?mode=mixagem")
        self.assertEqual(get_res.status_code, 200)
        body = get_res.get_json() or {}
        opts = body.get("data") or {}
        self.assertEqual(opts.get("mix_output_format"), "m4a")
        self.assertEqual(opts.get("mix_target_bitrate_kbps"), "96")
        self.assertEqual(opts.get("mix_max_size_mb"), "190")


if __name__ == "__main__":
    unittest.main()
