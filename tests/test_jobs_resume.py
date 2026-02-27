import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from mfaren.jobs import JobManager
from mfaren import transcribe_io as tio


class TestJobsResume(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mfaren_test_jobs_resume_")
        self.project_dir = os.path.join(self.tmpdir, "projeto")
        os.makedirs(os.path.join(self.project_dir, "convertido"), exist_ok=True)
        os.makedirs(os.path.join(self.project_dir, "normalizacao"), exist_ok=True)
        os.makedirs(os.path.join(self.project_dir, "vad"), exist_ok=True)
        os.makedirs(os.path.join(self.project_dir, "transcricao"), exist_ok=True)
        self.input_path = os.path.join(self.tmpdir, "audio.flac")
        with open(self.input_path, "wb") as f:
            f.write(b"x" * 128)

        for rel in (
            os.path.join("convertido", "audio_1.wav"),
            os.path.join("normalizacao", "audio_1.wav"),
            os.path.join("vad", "audio_1.wav"),
            os.path.join("transcricao", "audio_large-v3_abc_chunks.json"),
        ):
            full = os.path.join(self.project_dir, rel)
            with open(full, "w", encoding="utf-8") as f:
                f.write("{}")

        self.manager = JobManager.__new__(JobManager)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_prepare_resume_sets_chunk_resume_for_transcribe_stage(self):
        job = {
            "mode": "transcribe",
            "input_path": self.input_path,
            "message": "Etapa 4/5: Transcricao (52.0%)",
            "options": json.dumps({"mode": "transcribe", "project_dir": self.project_dir}, ensure_ascii=False),
        }
        out = self.manager._prepare_resume_options(job)
        opts = json.loads(out or "{}")
        self.assertEqual(opts.get("redo_from"), "transcribe")
        self.assertEqual(opts.get("transcribe_resume_chunks"), "on")
        self.assertTrue(str(opts.get("redo_chunk_checkpoint") or "").endswith("_chunks.json"))

    def test_prepare_resume_clears_chunk_resume_for_merge_stage(self):
        job = {
            "mode": "transcribe",
            "input_path": self.input_path,
            "message": "Etapa 5/5: Juncao (10.0%)",
            "options": json.dumps(
                {
                    "mode": "transcribe",
                    "project_dir": self.project_dir,
                    "transcribe_resume_chunks": "on",
                    "redo_chunk_checkpoint": "x",
                    "redo_chunk_index": 7,
                },
                ensure_ascii=False,
            ),
        }
        out = self.manager._prepare_resume_options(job)
        opts = json.loads(out or "{}")
        self.assertEqual(opts.get("redo_from"), "merge")
        self.assertNotIn("transcribe_resume_chunks", opts)
        self.assertNotIn("redo_chunk_checkpoint", opts)
        self.assertNotIn("redo_chunk_index", opts)

    def test_prepare_resume_uses_log_when_checkpoint_missing(self):
        cp_path = os.path.join(self.project_dir, "transcricao", "audio_large-v3_abc_chunks.json")
        if os.path.isfile(cp_path):
            os.remove(cp_path)
        live_path = tio.get_live_path("job-1", self.tmpdir)
        with open(live_path, "w", encoding="utf-8") as f:
            f.write("[00:00:00 --> 00:00:02] Falante 1 — teste\n")
        job = {
            "id": "job-1",
            "mode": "transcribe",
            "input_path": self.input_path,
            "message": "Etapa 4/5: Transcricao (52.0%)",
            "options": json.dumps(
                {"mode": "transcribe", "project_dir": self.project_dir, "output_dir": self.tmpdir},
                ensure_ascii=False,
            ),
        }
        with mock.patch("mfaren.jobs.trecov.infer_chunk_from_app_log", return_value={"suggested_chunk_index": 12}):
            out = self.manager._prepare_resume_options(job)
        opts = json.loads(out or "{}")
        self.assertEqual(opts.get("redo_from"), "transcribe")
        self.assertEqual(opts.get("transcribe_resume_chunks"), "on")
        self.assertEqual(opts.get("transcribe_resume_from_chunk_index"), 12)
        self.assertEqual(opts.get("resume_live_path"), live_path)


if __name__ == "__main__":
    unittest.main()
