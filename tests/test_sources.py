#!/usr/bin/env python3
"""The corresponding-source tool (vm/sources.py), against a fake snapshot.debian.org."""

import hashlib
import io
import json
import lzma
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm"))

import sources  # noqa: E402

DSC, ORIG = b"Format: 3.0 (quilt)\nSource: hello\n", b"orig tarball bytes" * 10
KDSC = b"Format: 3.0 (quilt)\nSource: linux\n"
ARCHIVE = "https://snapshot.debian.org/archive/debian/20260926T000000Z"
INDEX = f"{ARCHIVE}/dists/trixie/main/binary-arm64/Packages.xz"
PACKAGES = """Package: hello
Version: 2.10-3+b1
Architecture: arm64
Description: a greeting
 in two lines

Package: libhello1
Version: 2.10-3+b1

Package: linux-image-6.1-arm64
Version: 6.1-1
Built-Using: linux
 (= 6.1-1)
"""


def sha1(data):
    return hashlib.sha1(data).hexdigest()


class Snapshot:
    """snapshot.debian.org as far as the tool asks: binaries to sources,
    source files by digest; counts the file downloads."""

    def __init__(self):
        self.files = {sha1(DSC): DSC, sha1(ORIG): ORIG, sha1(KDSC): KDSC}
        self.indexes = {INDEX: PACKAGES}
        self.downloads = 0

    def get(self, url):
        url = urllib.parse.unquote(url)
        if url.endswith("/mr/binary/hello/"):
            return {"result": [{"name": "hello", "binary_version": "2.10-3+b1", "source": "hello", "version": "2.10-3"},
                               {"name": "hello", "binary_version": "2.10-2", "source": "hello", "version": "2.10-2"}]}
        if url.endswith("/mr/binary/libhello1/"):
            return {"result": [{"name": "libhello1", "binary_version": "2.10-3+b1", "source": "hello", "version": "2.10-3"}]}
        if url.endswith("/mr/binary/linux-image-6.1-arm64/"):
            return {"result": [{"name": "linux-image-6.1-arm64", "binary_version": "6.1-1",
                                "source": "linux-signed-arm64", "version": "6.1+1"}]}
        for source, version, dsc in (("linux-signed-arm64", "6.1+1", b"signatures"), ("linux", "6.1-1", KDSC)):
            if url.endswith(f"/mr/package/{source}/{version}/srcfiles?fileinfo=1"):
                self.files[sha1(dsc)] = dsc
                return {"fileinfo": {sha1(dsc): [{"name": f"{source}_{version}.dsc", "size": len(dsc),
                                                  "archive_name": "debian"}]}}
        if url.endswith("/mr/package/hello/2.10-3/srcfiles?fileinfo=1"):
            return {"fileinfo": {
                sha1(DSC): [{"name": "hello_2.10-3.dsc", "size": len(DSC), "archive_name": "debian"},
                            {"name": "hello_2.10-3.dsc", "size": len(DSC), "archive_name": "debian-debug"}],
                sha1(ORIG): [{"name": "hello_2.10.orig.tar.gz", "size": len(ORIG), "archive_name": "debian"}]}}
        raise sources.SourcesError(f"not in the snapshot: {url}")

    def open(self, url, timeout=None):
        if url.endswith("Packages.xz"):
            return io.BytesIO(lzma.compress(self.indexes[url].encode()))
        digest = url.rsplit("/", 1)[1]
        self.downloads += 1
        return io.BytesIO(self.files[digest])


class SourcesTests(unittest.TestCase):
    INFO = {"build_id": "b1",
            "debian": {"snapshot": "20260926T000000Z", "components": ["main"],
                       "archives": [{"url": ARCHIVE, "suites": ["trixie"]}]},
            "packages": [{"name": "hello", "version": "2.10-3+b1", "architecture": "arm64"},
                         {"name": "libhello1", "version": "2.10-3+b1", "architecture": "arm64"}],
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
        self.assertEqual((folder / "hello_2.10.orig.tar.gz").stat().st_mode & 0o777, 0o644, "published readable")
        self.assertEqual(json.loads((self.out / "SOURCES.json").read_text()), manifest)
        self.assertEqual(self.snapshot.downloads, 2)
        self.collect()
        self.assertEqual(self.snapshot.downloads, 2, "what is there and matches is not fetched again")

    def test_the_source_a_binary_was_built_using_comes_too(self):
        info = dict(self.INFO, kernel={"packages": {"linux-image-6.1-arm64": "6.1-1"}})
        manifest = self.collect(info)
        found = {(entry["source"], entry["version"]): entry["binaries"] for entry in manifest["sources"]}
        self.assertEqual(found[("linux-signed-arm64", "6.1+1")], ["linux-image-6.1-arm64 6.1-1"])
        self.assertEqual(found[("linux", "6.1-1")], ["linux-image-6.1-arm64 6.1-1 (Built-Using)"],
                         "the signed kernel's source holds only signatures: the kernel's own source is linux")
        self.assertEqual((self.out / "linux_6.1-1" / "linux_6.1-1.dsc").read_bytes(), KDSC)

    def test_a_binary_the_package_indexes_do_not_have_stops_the_collection(self):
        info = dict(self.INFO, kernel={"packages": {"linux-image-6.1-arm64": "6.1-2"}})
        with self.assertRaises(sources.SourcesError) as missing:
            self.collect(info)
        self.assertIn("linux-image-6.1-arm64 6.1-2", str(missing.exception))
        with self.assertRaises(sources.SourcesError):
            sources.indexes(dict(self.INFO, debian={"archives": [{"url": "https://example.com/debian", "suites": ["trixie"]}],
                                                    "components": ["main"]}))

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
