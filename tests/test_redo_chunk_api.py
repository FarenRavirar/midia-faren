import json
import os
import shutil
import tempfile
import unittest

import app as app_module


class _FakeJobManager:
    def __init__(self, job):
        self.job = job
        self.last_create = None

    def get_job(self, job_id):
        if self.job.get("id") == job_id:
            return self.job
        return None

    def create_jobs(self, items, options, source_type="url", input_paths=None, parent_job_id=None, meta=None):
        self.last_create = {
            "items": items,
            "options": dict(options or {}),
            "source_type": source_type,
            "input_paths": list(input_paths or []),
            "meta": list(meta or []),
        }
        return ["redo-job-1"]


class TestRedoChunkApi(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.old_manager = app_module.job_manager
        self.tmpdir = tempfile.mkdtemp(prefix="mfaren_test_redo_chunk_")
        self.project_dir = os.path.join(self.tmpdir, "proj")
        os.makedirs(self.project_dir, exist_ok=True)
        for stage in ("convertido", "normalizacao", "vad", "transcricao"):
            os.makedirs(os.path.join(self.project_dir, stage), exist_ok=True)

        self.input_path = os.path.join(self.tmpdir, "audio.flac")
        with open(self.input_path, "wb") as f:
            f.write(b"x" * 2048)

        self.raw_wav = os.path.join(self.project_dir, "convertido", "audio_2026.wav")
        self.norm_wav = os.path.join(self.project_dir, "normalizacao", "audio_norm_2026.wav")
        self.vad_wav = os.path.join(self.project_dir, "vad", "audio_vad_2026.wav")
        for p in (self.raw_wav, self.norm_wav, self.vad_wav):
            with open(p, "wb") as f:
                f.write(b"x" * 2048)

        self.checkpoint_path = os.path.join(self.project_dir, "transcricao", "audio_large-v3_deadbeef_chunks.json")
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "chunks": []}, f)

        self.job = {
            "id": "job-123",
            "source_type": "local",
            "input_path": self.input_path,
            "title": "audio",
            "channel": None,
            "options": json.dumps(
                {
                    "mode": "transcribe",
                    "project_dir": self.project_dir,
                    "output_dir": self.tmpdir,
                },
                ensure_ascii=False,
            ),
        }
        self.fake_manager = _FakeJobManager(self.job)
        app_module.job_manager = self.fake_manager

    def tearDown(self):
        app_module.job_manager = self.old_manager
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_redo_chunk_enqueues_transcribe_from_checkpoint(self):
        res = self.client.post(f"/api/jobs/{self.job['id']}/redo/chunk", json={"chunk_index": 2})
        self.assertEqual(res.status_code, 200)
        body = res.get_json() or {}
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("chunk_index"), 2)
        self.assertEqual(body.get("ids"), ["redo-job-1"])

        created = self.fake_manager.last_create
        self.assertIsNotNone(created)
        opts = created["options"]
        self.assertEqual(opts.get("redo_from"), "transcribe")
        self.assertEqual(opts.get("redo_chunk_index"), 1)
        self.assertEqual(opts.get("redo_chunk_checkpoint"), self.checkpoint_path)
        self.assertEqual(opts.get("transcribe_auto_recover"), "off")
        self.assertEqual(opts.get("transcribe_auto_recover_retries"), "0")
        self.assertEqual(opts.get("reuse_wav_raw"), self.raw_wav)
        self.assertEqual(opts.get("reuse_wav_norm"), self.norm_wav)
        self.assertEqual(opts.get("reuse_wav_vad"), self.vad_wav)


if __name__ == "__main__":
    unittest.main()
