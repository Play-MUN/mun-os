"""The standby picture's PPM reader (services/mun-shell/deploy/standby-frame.py,
read_ppm): the frame the shell grabs is read back before it is converted, and
a file cut short must fail at once, not hang the image build. Each case runs
in a process of its own with a time limit, so a reader that loops fails the
test instead of stopping the run."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "services/mun-shell/deploy/standby-frame.py"

# Loads the script as a module and prints read_ppm's answer as JSON:
# {"size": [w, h], "pixels": hex} or {"error": message}.
PROBE = """
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("standby_frame", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    width, height, pixels = module.read_ppm(Path(sys.argv[2]))
    print(json.dumps({"size": [width, height], "pixels": pixels.hex()}))
except ValueError as error:
    print(json.dumps({"error": str(error)}))
"""


class StandbyFrameReaderTests(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "frame.ppm"
            path.write_bytes(data)
            try:
                result = subprocess.run([sys.executable, "-I", "-c", PROBE, str(SCRIPT), str(path)],
                                        capture_output=True, text=True, timeout=10)
            except subprocess.TimeoutExpired:
                self.fail(f"read_ppm did not finish within 10 s on {data[:40]!r}")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_file_cut_short_fails_at_once(self):
        cases = {
            "empty": b"",
            "only the magic": b"P6",
            "the header without its depth": b"P6\n1920 1080\n",
            "a field cut at the end": b"P6\n1920 10",
            "a comment that never ends": b"P6\n# made by",
            "no byte after the depth": b"P6\n1 1\n255",
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertIn("error", self.read(data))

    def test_fields_and_pixels_must_be_the_formats(self):
        cases = {
            "another magic": b"P5\n1 1\n255\n\x01",
            "another depth": b"P6\n1 1\n65535\n\x01\x02\x03\x04\x05\x06",
            "a size that is not a number": b"P6\nx 1\n255\n\x01\x02\x03",
            "a size of zero": b"P6\n0 1\n255\n",
            "pixels short": b"P6\n2 1\n255\n\x01\x02\x03",
            "pixels past the size": b"P6\n1 1\n255\n\x01\x02\x03\x04",
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertIn("error", self.read(data))

    def test_a_whole_frame_is_read(self):
        self.assertEqual(self.read(b"P6\n1 1\n255\n\x01\x02\x03"), {"size": [1, 1], "pixels": "010203"})
        # As Qt's writer may put it: a comment, and spaces between fields.
        self.assertEqual(self.read(b"P6\n# made by Qt\n2  1\n255\n\x01\x02\x03\x04\x05\x06"),
                         {"size": [2, 1], "pixels": "010203040506"})


if __name__ == "__main__":
    unittest.main()
