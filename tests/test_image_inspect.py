"""`mun-card inspect` reports what the card's file system asks of a kernel on errors.

The offline check of the image (mun_card/ext4.py) describes the file system;
when it names the superblock's error behaviour, inspect passes it on. The
check's answer is stubbed here, over a real card image.
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import cli, image  # noqa: E402
from mun_card.errors import CardError  # noqa: E402


def have_e2fsprogs() -> bool:
    try:
        for tool in ("mke2fs", "debugfs", "dumpe2fs"):
            image.find_tool(tool)
        return True
    except CardError:
        return False


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
class InspectErrorsPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="card-inspect-"))
        cls.addClassCleanup(shutil.rmtree, cls.base, True)
        staging = cls.base / "staging"
        staging.mkdir()
        image.populate(staging, "valid")
        cls.card = cls.base / "demo.img"
        image.create_image(cls.card, staging, 8)

    def inspect(self, *arguments, policy="absent"):
        """inspect's status and output, the offline check answering with
        `policy` as the file system's error behaviour (or without the key)."""
        real = cli.ext4.check_mountable

        def check(path):
            described = dict(real(path))
            described.pop("errors_policy", None)
            if policy != "absent":
                described["errors_policy"] = policy
            return described
        out = io.StringIO()
        with patch.object(cli.ext4, "check_mountable", side_effect=check), contextlib.redirect_stdout(out):
            status = cli.main(["inspect", str(self.card), *arguments])
        return status, out.getvalue()

    def test_the_json_report_carries_the_policy_the_check_names(self):
        for policy in ("continue", "remount-ro", "panic"):
            with self.subTest(policy=policy):
                status, out = self.inspect("--json", policy=policy)
                report = json.loads(out)
                self.assertEqual((status, report["valid"]), (0, True))
                self.assertEqual(report["filesystem"]["errors_policy"], policy)
                self.assertEqual(set(report["filesystem"]), {"label", "uuid", "block_size", "errors_policy"})

    def test_a_check_that_does_not_name_it_is_reported_as_unknown(self):
        status, out = self.inspect("--json")
        self.assertEqual(status, 0)
        self.assertIsNone(json.loads(out)["filesystem"]["errors_policy"])

    def test_the_summary_mentions_it_only_when_the_card_asks_to_stop_the_system(self):
        status, out = self.inspect(policy="panic")
        self.assertEqual(status, 0)
        self.assertIn("errors=panic", out)
        self.assertIn("VÁLIDA", out)
        for policy in ("continue", "remount-ro", "absent"):
            with self.subTest(policy=policy):
                status, out = self.inspect(policy=policy)
                self.assertEqual(status, 0)
                self.assertNotIn("errors=", out)
                self.assertIn("VÁLIDA", out)


if __name__ == "__main__":
    unittest.main()
