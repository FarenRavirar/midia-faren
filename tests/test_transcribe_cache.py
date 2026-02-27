import os
import shutil
import tempfile
import unittest

from mfaren import transcribe_cache as tcache


class TestTranscribeCacheRetention(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mfaren_test_cache_")
        self.cache_root_prev = tcache.CACHE_ROOT
        self.manifest_prev = tcache.MANIFEST_PATH
        tcache.CACHE_ROOT = os.path.join(self.tmpdir, "cache")
        tcache.MANIFEST_PATH = os.path.join(self.tmpdir, "manifest.json")
        self.src_dir = os.path.join(self.tmpdir, "src")
        os.makedirs(self.src_dir, exist_ok=True)

    def tearDown(self):
        tcache.CACHE_ROOT = self.cache_root_prev
        tcache.MANIFEST_PATH = self.manifest_prev
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mk_src(self, name, mtime):
        path = os.path.join(self.src_dir, name)
        with open(path, "wb") as f:
            f.write(b"x" * 2048)
        os.utime(path, (mtime, mtime))
        return path

    def test_convert_cache_keeps_only_two_latest_per_base(self):
        p1 = self._mk_src("audio_2026-02-14_00-00-01.wav", 1000)
        p2 = self._mk_src("audio_2026-02-14_00-00-02.wav", 2000)
        p3 = self._mk_src("audio_2026-02-14_00-00-03.wav", 3000)

        tcache.cache_put("convert", "k1", {"wav": p1})
        tcache.cache_put("convert", "k2", {"wav": p2})
        tcache.cache_put("convert", "k3", {"wav": p3})

        manifest = tcache._load_manifest()  # noqa: SLF001 - teste focado em comportamento interno de retenção
        entries = manifest.get("entries") or {}
        convert_entries = {k: v for k, v in entries.items() if str(v.get("stage")) == "convert"}
        self.assertEqual(set(convert_entries.keys()), {"k2", "k3"})

        stage_dir = os.path.join(tcache.CACHE_ROOT, "convert")
        self.assertTrue(os.path.isdir(os.path.join(stage_dir, "k2")))
        self.assertTrue(os.path.isdir(os.path.join(stage_dir, "k3")))
        self.assertFalse(os.path.isdir(os.path.join(stage_dir, "k1")))


if __name__ == "__main__":
    unittest.main()
