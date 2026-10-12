"""Tests for the run publisher (``src.publish_run``).

Covers the Task 2 contract: validate then export an archived BacktestResult to
the frontend public artifacts, keep the public result byte-identical to the
archive, update the existing manifest format without duplicating entries, stay
idempotent, refuse invalid runs, and never mutate the archive.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import publish_run as pub  # noqa: E402

_FIXTURE = ROOT / "tests" / "fixtures" / "run_0001"
_LIVE = ROOT / "runs" / "run_0001"
# Prefer the committed fixture; the local runs/ archive is git-ignored.
ARCHIVE = _LIVE if (_LIVE / "result.json").is_file() else _FIXTURE


class PublishRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runs = self.root / "runs"
        self.public = self.root / "public"
        (self.runs / "run_0001").parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ARCHIVE, self.runs / "run_0001")
        self.archive_result = (self.runs / "run_0001" / "result.json").read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def _manifest(self):
        return json.loads(
            (self.public / pub.MANIFEST_NAME).read_text(encoding="utf-8")
        )

    def test_publish_copies_and_manifests(self):
        info = pub.publish_run("run_0001", runs=self.runs, public=self.public)
        dest = Path(info["published"])
        self.assertTrue(dest.is_file())
        # Public artifact equals the archived result byte-for-byte.
        self.assertEqual(dest.read_bytes(), self.archive_result)
        # Adapter-resolvable name: runs/run_0001/result.json -> /runs-run_0001-result.json
        self.assertEqual(dest.name, "runs-run_0001-result.json")
        self.assertEqual(pub.public_result_name("run_0001"), dest.name)
        manifest = self._manifest()
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(len(manifest["runs"]), 1)
        entry = manifest["runs"][0]
        self.assertEqual(entry["runId"], "run_0001")
        self.assertEqual(entry["file"], "runs/run_0001/result.json")
        self.assertTrue(entry["label"])
        self.assertTrue(entry["description"])

    def test_re_publish_is_idempotent(self):
        pub.publish_run("run_0001", runs=self.runs, public=self.public)
        info = pub.publish_run("run_0001", runs=self.runs, public=self.public)
        self.assertFalse(info["wrote_result"])
        self.assertFalse(info["wrote_manifest"])
        self.assertEqual(len(self._manifest()["runs"]), 1)
        self.assertEqual(Path(info["published"]).read_bytes(), self.archive_result)

    def test_manifest_updates_without_duplicating(self):
        self.public.mkdir(parents=True, exist_ok=True)
        stale = {
            "version": 1,
            "runs": [
                {"runId": "run_0000", "file": "runs/run_0000/result.json",
                 "label": "run_0000", "description": "other"},
                {"runId": "run_0001", "file": "runs/run_0001/result.json",
                 "label": "stale", "description": "stale"},
            ],
        }
        (self.public / pub.MANIFEST_NAME).write_text(
            json.dumps(stale, indent=2), encoding="utf-8"
        )
        pub.publish_run("run_0001", runs=self.runs, public=self.public)
        ids = [r["runId"] for r in self._manifest()["runs"]]
        # v1.0.1: newest publication first (run_0001 carries created_at; the
        # legacy run_0000 entry has none and sorts last).
        self.assertEqual(ids, ["run_0001", "run_0000"])
        entry = next(r for r in self._manifest()["runs"] if r["runId"] == "run_0001")
        self.assertNotEqual(entry["label"], "stale")
        self.assertTrue(entry.get("createdAt"))

    def test_manifest_orders_newest_first(self):
        pub.publish_run("run_0001", runs=self.runs, public=self.public)
        (self.runs / "run_0002").mkdir(parents=True, exist_ok=True)
        payload = json.loads((self.runs / "run_0001" / "result.json").read_text(encoding="utf-8"))
        payload["run_id"] = "run_0002"
        payload["created_at"] = "2099-01-01T00:00:00Z"
        (self.runs / "run_0002" / "result.json").write_text(json.dumps(payload), encoding="utf-8")
        pub.publish_run("run_0002", runs=self.runs, public=self.public)
        ids = [r["runId"] for r in self._manifest()["runs"]]
        self.assertEqual(ids, ["run_0002", "run_0001"])

    def test_invalid_run_is_not_published(self):
        (self.runs / "run_0001" / "result.json").write_text(
            json.dumps({"run_id": "run_0001"}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            pub.publish_run("run_0001", runs=self.runs, public=self.public)
        self.assertFalse((self.public / "runs-run_0001-result.json").exists())
        self.assertFalse((self.public / pub.MANIFEST_NAME).exists())

    def test_missing_run_raises(self):
        with self.assertRaises(FileNotFoundError):
            pub.publish_run("run_missing", runs=self.runs, public=self.public)

    def test_run_id_mismatch_rejected(self):
        payload = json.loads((self.runs / "run_0001" / "result.json").read_text(encoding="utf-8"))
        payload["run_id"] = "run_other"
        (self.runs / "run_0001" / "result.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            pub.publish_run("run_0001", runs=self.runs, public=self.public)

    def test_archive_is_not_mutated(self):
        before = hashlib.sha256(self.archive_result).hexdigest()
        before_names = sorted(p.name for p in (self.runs / "run_0001").iterdir())
        pub.publish_run("run_0001", runs=self.runs, public=self.public)
        after = hashlib.sha256(
            (self.runs / "run_0001" / "result.json").read_bytes()
        ).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(
            before_names, sorted(p.name for p in (self.runs / "run_0001").iterdir())
        )


if __name__ == "__main__":
    unittest.main()
