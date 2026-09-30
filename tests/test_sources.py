#!/usr/bin/env python3
"""The corresponding-source tool (vm/sources.py), against a fake snapshot.debian.org."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm"))

import sources  # noqa: E402

DSC, ORIG = b"Format: 3.0 (quilt)\nSource: hello\n", b"orig tarball bytes" * 10


def sha1(data):
    return hashlib.sha1(data).hexdigest()


class Snapshot:
    """snapshot.debian.org as far as the tool asks: binaries to sources,
    source files by digest; counts the file downloads."""

    def __init__(self):
        self.files = {sha1(DSC): DSC, sha1(ORIG): ORIG}
        self.downloads = 0

    def get(self, url):
        if url.endswith("/mr/binary/hello/"):
            return {"result": [{"name": "hello", "binary_version": "2.10-3+b1", "source": "hello", "version": "2.10-3"},
                               {"name": "hello", "binary_version": "2.10-2", "source": "hello", "version": "2.10-2"}]}
        if url.endswith("/mr/binary/libhello1/"):
            return {"result": [{"name": "libhello1", "binary_version": "2.10-3+b1", "source": "hello", "version": "2.10-3"}]}
        if url.endswith("/mr/package/hello/2.10-3/srcfiles?fileinfo=1"):
            return {"fileinfo": {
                sha1(DSC): [{"name": "hello_2.10-3.dsc", "size": len(DSC), "archive_name": "debian"},
                            {"name": "hello_2.10-3.dsc", "size": len(DSC), "archive_name": "debian-debug"}],
                sha1(ORIG): [{"name": "hello_2.10.orig.tar.gz", "size": len(ORIG), "archive_name": "debian"}]}}
        raise sources.SourcesError(f"not in the snapshot: {url}")

    def open(self, url, timeout=None):
        digest = url.rsplit("/", 1)[1]
        self.downloads += 1
        return io.BytesIO(self.files[digest])


class SourcesTests(unittest.TestCase):
    INFO = {"build_id": "b1", "debian": {"snapshot": "20260926T000000Z"},
            "packages": [{"name": "hello", "version": "2.10-3+b1"}, {"name": "libhello1", "version": "2.10-3+b1"}],
            "initrd": {"packages": [{"name": "hello", "version": "2.10-3+b1"}]}, "kernel": {"packages": {}}}

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-sources-")
        self.addCleanup(directory.cleanup)
        self.out = Path(directory.name) / "sources"
        self.snapshot = Snapshot()

    def collect(self, info=None, fetch=True):
        return sources.collect(info or self.INFO, self.out, fetch, get=self.snapshot.get, opener=self.snapshot.open,
                               report=lambda message: None)

    def test_each_source_is_fetched_once_checked_and_listed_with_what_it_built(self):
        manifest = self.collect()
        self.assertEqual((manifest["format"], manifest["build_id"], manifest["snapshot"]), ("mun-sources/1", "b1", "20260926T000000Z"))
        [entry] = manifest["sources"]
        self.assertEqual((entry["source"], entry["version"]), ("hello", "2.10-3"), "the binNMU's source, not the other version")
        self.assertEqual(entry["binaries"], ["hello 2.10-3+b1", "libhello1 2.10-3+b1"], "the initrd's copy counted once")
        self.assertEqual([f["name"] for f in entry["files"]], ["hello_2.10-3.dsc", "hello_2.10.orig.tar.gz"])
        folder = self.out / "hello_2.10-3"
        self.assertEqual((folder / "hello_2.10-3.dsc").read_bytes(), DSC)
        self.assertEqual((folder / "hello_2.10.orig.tar.gz").read_bytes(), ORIG)
        self.assertEqual(json.loads((self.out / "SOURCES.json").read_text()), manifest)
        self.assertEqual(self.snapshot.downloads, 2)
        self.collect()
        self.assertEqual(self.snapshot.downloads, 2, "what is there and matches is not fetched again")

    def test_a_file_that_does_not_match_its_digest_is_not_kept(self):
        self.snapshot.files[sha1(ORIG)] = b"tampered" * 20
        with self.assertRaises(sources.SourcesError):
            self.collect()
        folder = self.out / "hello_2.10-3"
        self.assertFalse((folder / "hello_2.10.orig.tar.gz").exists())
        self.assertEqual([p.name for p in folder.iterdir() if p.name.endswith(".part")], [], "no partial file left")

    def test_a_package_the_snapshot_cannot_account_for_stops_the_collection(self):
        info = dict(self.INFO, packages=self.INFO["packages"] + [{"name": "mystery", "version": "1.0"}])
        with self.assertRaises(sources.SourcesError):
            self.collect(info)
        with self.assertRaises(sources.SourcesError):
            sources.binaries(dict(self.INFO, packages=[{"name": "../etc", "version": "1"}]))

    def test_listing_fetches_nothing(self):
        manifest = self.collect(fetch=False)
        self.assertEqual(len(manifest["sources"]), 1)
        self.assertEqual(self.snapshot.downloads, 0)
        self.assertFalse((self.out / "hello_2.10-3").exists())


if __name__ == "__main__":
    unittest.main()
