"""The services' staging scripts (services/*/deploy/stage.sh), which the MUN OS
image build runs. Run for real into temporary directories; no root, packages
or systemd are needed."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StagingTests(unittest.TestCase):
    """The staging scripts the image build runs."""

    def stage(self, *args):
        result = subprocess.run(["sh", *map(str, args)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_card_service_files_land_under_destdir_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            src = ROOT / "services" / "mun-cardd"
            self.stage(src / "deploy" / "stage.sh", src, ROOT / "tools" / "mun-card" / "mun_card", dest)
            self.assertTrue(os.access(dest / "opt/mun/cardd/cardd.py", os.X_OK))
            self.assertTrue((dest / "opt/mun/cardd/lib/mun_card/validate.py").is_file())
            self.assertTrue((dest / "etc/systemd/system/mun-cardd.service").is_file())
            self.assertIn("g mun-shell", (dest / "usr/lib/sysusers.d/mun-cardd.conf").read_text())
            record = (dest / "opt/mun/cardd/BUILD-INFO").read_text()
            self.assertRegex(record, r"source_sha256=[0-9a-f]{64}\n")
            self.assertNotIn("installed=", record, "a staged record does not depend on when it was staged")

    def test_launcher_files_and_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            src = ROOT / "services" / "mun-launchd"
            self.stage(src / "deploy" / "stage.sh", src, dest)
            for unit in ("run-mun-launch.mount", "mun-shell-restore.service",
                         "mun-launch-cleanup@.service", "mun-launchd.service"):
                self.assertTrue((dest / "etc/systemd/system" / unit).is_file(), unit)
            self.assertTrue(os.access(dest / "usr/local/libexec/mun-launch-cleanup", os.X_OK))
            users = (dest / "usr/lib/sysusers.d/mun-launchd.conf").read_text()
            self.assertIn('u mun-game - "MUN game sessions" /nonexistent /usr/sbin/nologin', users)
            for group in ("video", "input", "render", "audio"):
                self.assertIn(f"m mun-game {group}", users)

    def test_the_same_sources_give_the_same_staged_record(self):
        records = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                src = ROOT / "services" / "mun-launchd"
                self.stage(src / "deploy" / "stage.sh", src, tmp)
                records.append((Path(tmp) / "opt/mun/launchd/BUILD-INFO").read_text())
        self.assertEqual(records[0], records[1])

    def test_shell_staging_needs_a_build_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = ROOT / "services" / "mun-shell"
            result = subprocess.run(["sh", str(src / "deploy" / "stage.sh"), str(src), str(Path(tmp) / "nobuild"), tmp],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("run deploy/build.sh first", result.stderr)

    def test_the_content_mount_keeps_its_earlier_name_as_a_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = ROOT / "services" / "mun-launchd"
            self.stage(src / "deploy" / "stage.sh", src, tmp)
            rules = (Path(tmp) / "usr/lib/tmpfiles.d/mun-launchd.conf").read_text().splitlines()
        rules = [line.split() for line in rules if line and not line.startswith("#")]
        self.assertIn(["L", "/run/neptune/card", "-", "-", "-", "-", "/run/mun/card"], rules)
        self.assertIn(["d", "/run/mun/card", "0755", "root", "root", "-"], rules)
        self.assertEqual({rule[1] for rule in rules if rule[1].startswith("/run/neptune")},
                         {"/run/neptune", "/run/neptune/card"}, "nothing else keeps the earlier name")

    def test_nothing_staged_carries_the_earlier_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            self.stage(ROOT / "services/mun-cardd/deploy/stage.sh", ROOT / "services/mun-cardd",
                       ROOT / "tools/mun-card/mun_card", dest)
            self.stage(ROOT / "services/mun-launchd/deploy/stage.sh", ROOT / "services/mun-launchd", dest)
            for path in dest.rglob("*"):
                self.assertNotIn("neptune", str(path.relative_to(dest)), path)
                if path.is_file() and path.suffix in ("", ".service", ".mount", ".conf"):
                    for number, line in enumerate(path.read_text().splitlines(), 1):
                        if line.startswith("#") or "/run/neptune" in line:
                            continue   # comments explain the earlier names; the content link is by design
                        self.assertNotIn("neptune", line, f"{path.relative_to(dest)}:{number}")


if __name__ == "__main__":
    unittest.main()
