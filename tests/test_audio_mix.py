import json
import os
import shutil
import tempfile
import unittest
import zipfile

from mfaren import audio_mix as mix


class TestAudioMix(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mfaren_test_audio_mix_")
        self.output_dir = os.path.join(self.tmpdir, "out")
        os.makedirs(self.output_dir, exist_ok=True)

        self.old_find_ffmpeg = mix.tio.find_ffmpeg
        self.old_ffprobe_duration = mix.tio.ffprobe_duration
        self.old_run_ffmpeg_stage = mix.texec.run_ffmpeg_stage

        mix.tio.find_ffmpeg = lambda: "ffmpeg"
        mix.tio.ffprobe_duration = lambda path: 120.0

    def tearDown(self):
        mix.tio.find_ffmpeg = self.old_find_ffmpeg
        mix.tio.ffprobe_duration = self.old_ffprobe_duration
        mix.texec.run_ffmpeg_stage = self.old_run_ffmpeg_stage
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_zip(self, name, files):
        path = os.path.join(self.tmpdir, name)
        with zipfile.ZipFile(path, "w") as zf:
            for rel, data in files.items():
                zf.writestr(rel, data)
        return path

    def test_zip_sem_audio_retorna_erro(self):
        zip_path = self._write_zip("sessao.zip", {"readme.txt": b"x"})

        with self.assertRaises(RuntimeError):
            mix.build_audio_mix(
                zip_path,
                {
                    "output_dir": self.output_dir,
                    "mix_output_format": "m4a",
                    "mix_target_bitrate_kbps": "96",
                    "mix_max_size_mb": "190",
                },
                progress_cb=None,
                cancel_event=None,
                logger=None,
                pid_cb=None,
            )

    def test_estimate_bitrate_for_limit(self):
        kbps = mix._estimate_bitrate_for_limit(190 * 1024 * 1024, 3600.0)
        self.assertGreaterEqual(kbps, 24)
        self.assertLessEqual(kbps, 512)

    def test_fluxo_basico_com_autofit(self):
        zip_path = self._write_zip("sessao.zip", {"faixa1.flac": b"fake-audio"})

        def fake_run_ffmpeg_stage(cmd, duration, stage_label, progress_cb=None, cancel_event=None, pid_cb=None, logger=None, report_cb=None):
            if progress_cb:
                progress_cb({"percent": 100.0, "speed": "1.0x", "eta_seconds": 0, "downloaded_bytes": 1024})
            out_path = cmd[-1]
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            size_bytes = 4096
            if stage_label == "Exportacao":
                if "-b:a" in cmd:
                    bidx = cmd.index("-b:a") + 1
                    bitrate = int(str(cmd[bidx]).replace("k", ""))
                else:
                    bitrate = 1536
                size_bytes = int((bitrate * 1000 / 8) * float(duration))
                size_bytes = max(size_bytes, 4096)

            with open(out_path, "wb") as f:
                f.write(b"x" * size_bytes)

        mix.texec.run_ffmpeg_stage = fake_run_ffmpeg_stage

        output_path, meta = mix.build_audio_mix(
            zip_path,
            {
                "output_dir": self.output_dir,
                "mix_output_format": "m4a",
                "mix_target_bitrate_kbps": "256",
                "mix_max_size_mb": "1",
            },
            progress_cb=None,
            cancel_event=None,
            logger=None,
            pid_cb=None,
        )

        self.assertTrue(os.path.isfile(output_path))
        self.assertEqual(meta.get("channel"), "nao informado")

        base_no_ext, _ = os.path.splitext(output_path)
        sidecar = f"{base_no_ext}.json"
        self.assertTrue(os.path.isfile(sidecar))
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("output_format"), "m4a")
        self.assertLessEqual(int(data.get("size_bytes") or 0), int(data.get("size_limit_bytes") or 0))
        self.assertLess(int(data.get("bitrate_kbps") or 9999), 256)


if __name__ == "__main__":
    unittest.main()
