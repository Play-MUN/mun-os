"""How the card package judges one unit of a directory save (docs/saves.md):
the rule the console's launcher applies when it restores, kept here for the
card tool, which must not carry a save the console would refuse, nor spend
without bound on a unit a card's author wrote."""

import base64
import hashlib
import json
import sys
import time
import tracemalloc
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import saves  # noqa: E402

MIB = 1024 * 1024


def xml_unit(encoding: str, body: bytes = b'<Flag a="1"/>') -> bytes:
    return b'<?xml version="1.0" encoding="' + encoding.encode("ascii") + b'"?>\n' + body + b"\n"


# Encodings the XML parser cannot read: a name it does not know, multibyte
# ones (a Japanese engine's own), and codecs that are not character sets.
UNREADABLE = ("x-nope", "shift_jis", "euc-jp", "gb2312", "utf-7", "idna", "zlib")
JAPANESE = '<?xml version="1.0" encoding="Shift_JIS"?>\n<セーブ 名前="テスト"/>\n'.encode("shift_jis")
INFO = {"id": "sample.lab", "saves_directory": ".Sample/save", "saves_units": ["*.sav", "screen-*.zsc"],
        "saves_checks": ["zlib-xml", "zlib"], "saves_max_bytes": MIB}


def envelope(files: dict) -> dict:
    return {"format": "neptune-save/1", "game": "sample.lab", "schema": 1, "payload_kind": "files",
            "payload": {"files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                   "data": base64.b64encode(data).decode("ascii")} for name, data in files.items()]}}


class SaveUnitEncodingTests(unittest.TestCase):
    """A unit in an encoding the console cannot read fails its check; it is
    never an exception."""

    def test_an_unreadable_encoding_fails_the_check_with_its_reason(self):
        for check, wrap in (("xml", bytes), ("zlib-xml", zlib.compress)):
            for encoding in UNREADABLE:
                with self.subTest(check=check, encoding=encoding):
                    self.assertIn("codificación", saves.unit_check(check, wrap(xml_unit(encoding))))
            with self.subTest(check=check, encoding="a real Shift-JIS document"):
                self.assertIn("codificación", saves.unit_check(check, wrap(JAPANESE)))
        for encoding in ("utf-16", "UTF-16LE"):
            with self.subTest(encoding=encoding + " declared over single-byte text"):
                self.assertIsNotNone(saves.unit_check("xml", xml_unit(encoding)))

    def test_the_encodings_the_parser_reads_still_pass(self):
        accented = '<Nombre jugador="Iván Peña"/>'
        for encoding, codec in (("UTF-8", "utf-8"), ("utf-8", "utf-8"), ("US-ASCII", "ascii"), ("ISO-8859-1", "latin-1"),
                                ("windows-1252", "cp1252")):
            body = (accented if codec != "ascii" else '<Nombre jugador="Ivan"/>').encode(codec)
            with self.subTest(encoding=encoding):
                self.assertIsNone(saves.unit_check("xml", xml_unit(encoding, body)))
                self.assertIsNone(saves.unit_check("zlib-xml", zlib.compress(xml_unit(encoding, body) + b"\0")))
        self.assertIsNotNone(saves.unit_check("xml", xml_unit("UTF-8", accented.encode("latin-1"))),
                             "bytes that are not the declared encoding are still a damaged unit")

    def test_a_save_holding_such_a_unit_is_refused_not_raised(self):
        problem = saves.envelope_problem(envelope({"save-0000.sav": zlib.compress(xml_unit("x-nope"))}), INFO, "neptune-save/1")
        self.assertIn("save-0000.sav", problem); self.assertIn("codificación", problem)
        self.assertIsNone(saves.envelope_problem(
            envelope({"save-0000.sav": zlib.compress(xml_unit("ISO-8859-1", '<N j="Iván"/>'.encode("latin-1")))}),
            INFO, "neptune-save/1"))


class SaveUnitCostTests(unittest.TestCase):
    """What checking one unit may cost, whatever the unit holds."""

    MARKUP = b"<a b='1'/>"

    def checked(self, check: str, unit: bytes, *limit) -> "tuple":
        tracemalloc.start()
        started = time.monotonic()
        try:
            problem = saves.unit_check(check, unit, *limit)
            return problem, tracemalloc.get_traced_memory()[1], time.monotonic() - started
        finally:
            tracemalloc.stop()

    def test_a_small_unit_that_inflates_to_64_mib_of_markup_is_refused_cheaply(self):
        unit = zlib.compress(self.MARKUP * ((64 * MIB - 64) // len(self.MARKUP)), 9)
        self.assertLess(len(unit), 160 * 1024, "inside every bound a card may declare")
        problem, peak, seconds = self.checked("zlib-xml", unit)
        self.assertIn("límite", problem or "accepted")
        self.assertLess(peak, 64 * MIB, f"peak {peak / MIB:.0f} MiB in {seconds:.1f} s")
        self.assertLess(seconds, 30, "under tracing; a fraction of a second without it")

    def test_the_largest_xml_a_unit_may_hold_is_checked_without_building_a_document(self):
        body = self.MARKUP * (saves.XML_INFLATE_LIMIT // len(self.MARKUP))
        plain = self.MARKUP * (8 * MIB // len(self.MARKUP))        # the most a card may declare
        for check, unit in (("zlib-xml", zlib.compress(body, 1)), ("xml", plain)):
            with self.subTest(check):
                problem, peak, seconds = self.checked(check, unit)
                self.assertIsNone(problem)
                self.assertLess(peak, 3 * saves.XML_INFLATE_LIMIT, f"peak {peak / MIB:.0f} MiB in {seconds:.1f} s")
        self.assertIn("XML incompleto", saves.unit_check("zlib-xml", zlib.compress(plain + b"<a><b></a>", 1)),
                      "and what it holds is still read to its end")
        self.assertIn("límite", saves.unit_check("zlib-xml", zlib.compress(body + self.MARKUP, 1)))

    def test_saves_of_the_size_real_engines_write_still_pass(self):
        rows = "".join(f'<Entity id="{n}" x="{n * 3.5:.2f}" y="{n * 1.25:.2f}" state="idle"><Flag name="seen{n}" v="1"/>'
                       f"<Note>café &amp; té {n}</Note></Entity>\n" for n in range(4000)).encode("utf-8")
        save = b"<?xml version='1.0' encoding='UTF-8'?>\n<Version major='1'/>\n<World>" + rows + b"</World>\n\0"
        self.assertGreater(len(save), 400 * 1024)
        self.assertIsNone(saves.unit_check("zlib-xml", zlib.compress(save)))
        self.assertIsNone(saves.unit_check("xml", save))
        thumbnail = zlib.compress(bytes(4 * MIB + 18))            # a 1024x1024 RGBA picture, all black
        self.assertLess(len(thumbnail), 8192)
        self.assertIsNone(saves.unit_check("zlib", thumbnail))

    def test_a_unit_may_inflate_only_so_far_beyond_what_the_card_declared_for_its_saves(self):
        self.assertEqual(saves.inflate_limit(8 * MIB), saves.INFLATE_LIMIT, "the absolute bound")
        self.assertEqual((saves.INFLATE_LIMIT, saves.XML_INFLATE_LIMIT), (64 * MIB, 16 * MIB))
        small = saves.inflate_limit(4096)
        self.assertEqual(small, 64 * 4096)
        self.assertIsNone(saves.unit_check("zlib", zlib.compress(bytes(small)), small))
        self.assertIn("límite", saves.unit_check("zlib", zlib.compress(bytes(small + 1)), small))
        # In a save: the bound is the card's own.
        info = dict(INFO, saves_max_bytes=4096)
        self.assertIsNone(saves.envelope_problem(envelope({"screen-0000.zsc": zlib.compress(bytes(small))}), info, "neptune-save/1"))
        problem = saves.envelope_problem(envelope({"screen-0000.zsc": zlib.compress(bytes(small + 1))}), info, "neptune-save/1")
        self.assertIn("límite", problem or "accepted")


class SaveUnitRuleTests(unittest.TestCase):
    """The rest of the rule, unchanged: what a complete unit is."""

    def test_complete_and_incomplete_units(self):
        slot = b"<?xml version='1.0'?>\n<Flag a='1'/><Pos x='2' y='3'/>\n\0"     # several top-level elements, a final NUL
        self.assertIsNone(saves.unit_check("zlib-xml", zlib.compress(slot)))
        self.assertIsNone(saves.unit_check("xml", b"\xef\xbb\xbf" + slot))
        self.assertIsNone(saves.unit_check("any", b"\x00anything"))
        self.assertIsNone(saves.unit_check("zlib", zlib.compress(b"\x00\x10" * 5000)))
        cases = {"not zlib": ("zlib-xml", b"not zlib"), "torn zlib": ("zlib-xml", zlib.compress(slot)[:-4]),
                 "data after the stream": ("zlib", zlib.compress(b"x") + b"more"),
                 "torn XML": ("xml", b"<Flag a='1'><Pos"), "a NUL inside": ("xml", b"<Flag a='1'/>\0<Pos/>"),
                 "a document type": ("xml", b"<!DOCTYPE x [<!ENTITY e 'e'>]><x>&e;</x>"),
                 "an unfinished declaration": ("xml", b"<?xml version='1.0'"),
                 "an undefined entity": ("xml", b"<x>&nope;</x>")}
        for what, (check, data) in cases.items():
            with self.subTest(what):
                self.assertIsInstance(saves.unit_check(check, data), str)

    def test_an_ordinary_save_is_one_the_console_would_restore(self):
        slot = zlib.compress(b"<?xml version='1.0'?>\n<Flag a='1'/><Pos x='2' y='3'/>\n\0")
        thumb = zlib.compress(b"\x00\x10" * 5000)
        document = json.loads(json.dumps(envelope({"save-0000.sav": slot, "screen-0000.zsc": thumb})))
        self.assertIsNone(saves.envelope_problem(document, INFO, "neptune-save/1"))
        self.assertIn("save-0000.sav", saves.envelope_problem(envelope({"save-0000.sav": slot[:-4]}), INFO, "neptune-save/1"))


if __name__ == "__main__":
    unittest.main()
