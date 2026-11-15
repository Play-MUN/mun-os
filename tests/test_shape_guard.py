"""The shell's guard against an image whose decoding takes it down
(services/mun-shell, "Decoding"): a marker in the runtime directory names
the insertion whose images are being read, so that a start which finds one
skips that insertion instead of reading the same image again. The marker's
file side is checked on the host with the shell's other rules
(services/mun-shell/tests/decodingmarker_test.cpp); here, that every read of
a card's images is inside the guard.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "services" / "mun-shell"


def function(text: str, signature: str) -> str:
    """The body of the function that starts with `signature`, to the first
    line that closes it."""
    start = text.index(signature)
    return text[start:text.index("\n}\n", start)]


class DecodingGuardTests(unittest.TestCase):
    def source(self, name):
        return (SHELL / "src" / name).read_text(encoding="utf-8")

    def test_the_worlds_images_are_read_under_the_marker(self):
        # The world's images are read on the painter's thread: their headers
        # (estimateFor) and their pixels (load). The marker is written before
        # the first and removed after the last; if it cannot be written,
        # nothing is read.
        world = self.source("shapeworld.cpp")
        self.assertTrue('#include "decodingmarker.h"' in world, "the painter does not use the marker")
        prepare = function(world, "void WorldPainter::prepare(")
        written = prepare.index("decodingmarker::write(")
        refused = prepare.index("return;", written)
        headers = prepare.index("estimateFor(")
        pixels = prepare.index("load(detail")
        removed = prepare.index("QFile::remove(marker)")
        self.assertLess(written, refused)
        self.assertLess(refused, headers, "a marker that cannot be written ends the preparation before any read")
        self.assertLess(headers, pixels)
        self.assertLess(pixels, removed)
        # Nothing else of the painter reads a file: decode() and header() are
        # called from load() and estimateFor() only.
        for name in ("WorldPainter::setDetail(", "WorldPainter::tick(", "WorldPainter::freeAbove(",
                     "WorldPainter::composeStill("):
            self.assertNotRegex(function(world, f"void {name}"), r"\b(decode|header|headerOf|load)\(", name)

    def test_the_worlds_marker_is_the_one_a_start_reads_and_a_clean_stop_removes(self):
        world = self.source("shapeworld.cpp")
        self.assertIn('QStringLiteral("/shape-decoding")', world)
        self.assertIn('runtimeFile("shape-decoding")', self.source("shape.cpp"))
        unit = (SHELL / "deploy" / "mun-shell.service").read_text(encoding="utf-8")
        stop = re.search(r"(?m)^ExecStopPost=(.+)$", unit).group(1)
        self.assertIn('"$$XDG_RUNTIME_DIR/shape-decoding"', stop)

    def test_the_window_and_the_cover_are_decoded_only_under_a_written_marker(self):
        # Writing the marker can fail (a full or damaged runtime directory):
        # then a crash while decoding would leave no trace, so the loader
        # must know the marker is there before it decodes, and decode
        # nothing when it is not.
        loader = function(self.source("shape.cpp"), "void ShapeLoader::load(")
        self.assertTrue("decodingmarker::write(" in loader, "the marker's write is not checked")
        self.assertNotIn("writeMarker(marker", loader)
        written = loader.index("decodingmarker::write(")
        decodes = [match.start() for match in re.finditer(r"= decode\(&", loader)]
        self.assertEqual(len(decodes), 2, "the card window and the cover")
        for at in decodes:
            self.assertLess(written, at)
            condition = loader[loader.rindex("if (", 0, at):at]
            self.assertIn("guarded", condition, condition)
        self.assertLess(max(decodes), loader.index("QFile::remove(marker)"))

    def test_an_insertion_skipped_after_a_crash_stays_skipped_over_clean_restarts(self):
        # The launcher stops the shell around every game: a skip held in
        # memory only would be forgotten then, and the image that ended the
        # shell read again after every game. What a start learns from a
        # marker is kept in a file of its own, which a clean stop does not
        # remove.
        start = function(self.source("shape.cpp"), "Shape::Shape(QObject *parent)")
        for marker, kept in (("shape-decoding", "shape-skipped"), ("shape-decoding-motion", "shape-skipped-motion")):
            self.assertRegex(start, rf'decodingmarker::resume\(\s*\w+\("{marker}"\),\s*\w+\("{kept}"\)\)')
        unit = (SHELL / "deploy" / "mun-shell.service").read_text(encoding="utf-8")
        stop = re.search(r"(?m)^ExecStopPost=(.+)$", unit).group(1)
        self.assertNotIn("shape-skipped", stop, "a clean stop forgets only what was being decoded")

    def test_the_marker_is_built_and_run_with_the_host_checks(self):
        recipe = (SHELL / "tests" / "Makefile").read_text(encoding="utf-8")
        self.assertIn("\t./decodingmarker_test\n", recipe)
        self.assertIn("decodingmarker_test: decodingmarker_test.cpp ../src/decodingmarker.h", recipe)


if __name__ == "__main__":
    unittest.main()
