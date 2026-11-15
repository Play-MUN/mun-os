"""What a PNG may carry besides its pixels (docs/shape.md, "Files"): the
checker bounds the ancillary chunks of every image it accepts, because
whatever reads such an image's header keeps its text and its colour profile
in memory, and inflates the compressed ones, before a pixel is decoded.

The images are made here, chunk by chunk; nothing is decoded.
"""

import json
import shutil
import struct
import sys
import tempfile
import tracemalloc
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import motion, shape, shapetools, validate  # noqa: E402
from mun_card.source import DirectorySource  # noqa: E402

KIB = 1024


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def with_chunks(image: bytes, *chunks: bytes, after_data: bool = False) -> bytes:
    """`image` with `chunks` put after its IHDR (or after its last IDAT)."""
    at = len(image) - 12 if after_data else 33
    return image[:at] + b"".join(chunks) + image[at:]


def flat(width: int = 64, height: int = 36) -> bytes:
    return shapetools.encode_png(width, height, [bytes((8, 40, 56, 255)) * width] * height, alpha=True)


def ztxt(size: int, keyword: bytes = b"Comment") -> bytes:
    """A compressed text chunk that inflates to `size` bytes."""
    return chunk(b"zTXt", keyword + b"\0\0" + zlib.compress(b"A" * size, 9))


def itxt(text: bytes, compressed: bool = False, keyword: bytes = b"XML:com.adobe.xmp") -> bytes:
    body = zlib.compress(text, 9) if compressed else text
    return chunk(b"iTXt", keyword + b"\0" + (b"\1" if compressed else b"\0") + b"\0" + b"\0" + b"\0" + body)


def iccp(size: int) -> bytes:
    """A colour profile chunk whose stream inflates to `size` bytes."""
    return chunk(b"iCCP", b"profile\0\0" + zlib.compress(b"\0" * size, 9))


def text_bomb(image: bytes, budget: int) -> bytes:
    """`image` with as many text chunks as fit in `budget` bytes, each a few
    KiB that inflate to 8 MB: the header alone makes its reader hold
    gigabytes."""
    one = ztxt(7_999_000)
    return with_chunks(image, *[one] * ((budget - len(image)) // len(one)))


def ordinary(image: bytes) -> bytes:
    """`image` as common tools write it: resolution, gamma, chromaticities,
    a colour profile the size of sRGB's, a time, EXIF, the program's name, a
    comment, an XMP packet and a compressed text."""
    profile = bytes(range(256)) * 12 + bytes(72)        # 3144 bytes, sRGB IEC61966-2.1's size
    xmp = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
           + b"<rdf:li>history</rdf:li>" * 300 + b"</x:xmpmeta>")
    return with_chunks(
        image,
        chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
        chunk(b"gAMA", struct.pack(">I", 45455)),
        chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)),
        chunk(b"iCCP", b"sRGB IEC61966-2.1\0\0" + zlib.compress(profile, 9)),
        chunk(b"bKGD", bytes(6)),
        chunk(b"tIME", struct.pack(">HBBBBB", 2026, 11, 14, 12, 0, 0)),
        chunk(b"eXIf", b"MM\0*" + bytes(2048)),
        chunk(b"tEXt", b"Software\0An ordinary image editor 1.0"),
        chunk(b"tEXt", b"Comment\0Made for a test"),
        itxt(xmp),
        ztxt(2000, b"Description"),
    )


def refusal(data: bytes):
    """The checker's reason for refusing `data` as an image, or None."""
    try:
        shape.png_header(data)
    except shape._Invalid as exc:
        return exc.code, exc.detail
    return None


class AncillaryBoundsTests(unittest.TestCase):
    def test_ordinary_metadata_is_accepted(self):
        image = ordinary(flat())
        self.assertIsNone(refusal(image))
        self.assertIsNone(refusal(with_chunks(flat(), chunk(b"tEXt", b"Software\0x"), after_data=True)),
                          "text may also follow the image data")
        self.assertIsNone(validate.png_ancillary_excess(image))

    def test_text_that_inflates_without_bound_is_refused(self):
        bomb = text_bomb(flat(256, 144), shape.IMAGE_MAX_BYTES - 4096)
        self.assertLessEqual(len(bomb), shape.IMAGE_MAX_BYTES)
        self.assertGreater(bomb.count(b"zTXt"), 500)
        code, detail = refusal(bomb)
        self.assertEqual(code, "shape_png")
        self.assertIn("auxiliares", detail)

    def test_the_text_chunks_are_counted(self):
        note = chunk(b"tEXt", b"Comment\0x")
        self.assertIsNone(refusal(with_chunks(flat(), *[note] * validate.PNG_TEXT_MAX_CHUNKS)))
        self.assertEqual(refusal(with_chunks(flat(), *[note] * (validate.PNG_TEXT_MAX_CHUNKS + 1)))[0], "shape_png")
        # zTXt, iTXt and tEXt count together, wherever they are
        mixed = [ztxt(10), itxt(b"x"), note] * 5 + [note]
        self.assertEqual(len(mixed), validate.PNG_TEXT_MAX_CHUNKS)
        self.assertIsNone(refusal(with_chunks(flat(), *mixed)))
        self.assertEqual(refusal(with_chunks(with_chunks(flat(), *mixed), note, after_data=True))[0], "shape_png")

    def test_the_text_is_bounded_as_stored(self):
        def one(size):
            return chunk(b"tEXt", b"Comment\0" + b"x" * (size - 8))
        self.assertIsNone(refusal(with_chunks(flat(), one(validate.PNG_TEXT_MAX_BYTES))))
        self.assertEqual(refusal(with_chunks(flat(), one(validate.PNG_TEXT_MAX_BYTES + 1)))[0], "shape_png")
        half = one(validate.PNG_TEXT_MAX_BYTES // 2)
        self.assertIsNone(refusal(with_chunks(flat(), half, half)))
        self.assertEqual(refusal(with_chunks(flat(), half, half, chunk(b"tEXt", b"k\0")))[0], "shape_png")

    def test_what_compressed_text_and_the_profile_inflate_to_is_bounded(self):
        limit = validate.PNG_EXPANDED_MAX_BYTES
        # a few hundred stored bytes each: only inflating them tells
        self.assertLess(len(ztxt(limit)), 2 * KIB)
        self.assertIsNone(refusal(with_chunks(flat(), ztxt(limit - 64))))
        self.assertEqual(refusal(with_chunks(flat(), ztxt(limit + 1)))[0], "shape_png")
        self.assertEqual(refusal(with_chunks(flat(), itxt(b"A" * (limit + 1), compressed=True)))[0], "shape_png")
        self.assertEqual(refusal(with_chunks(flat(), iccp(limit + 1)))[0], "shape_png")
        self.assertIsNone(refusal(with_chunks(flat(), iccp(limit - 64))))
        # together: the text and the profile share the bound
        self.assertEqual(refusal(with_chunks(flat(), iccp(limit // 2), ztxt(limit // 2), ztxt(64)))[0], "shape_png")
        # never inflated past the bound, however much the stream holds
        huge = with_chunks(flat(), chunk(b"zTXt", b"k\0\0" + zlib.compress(bytes(24 * KIB * KIB), 9)))
        self.assertLess(len(huge), validate.PNG_TEXT_MAX_BYTES)
        tracemalloc.start()
        try:
            self.assertEqual(refusal(huge)[0], "shape_png")
            self.assertLess(tracemalloc.get_traced_memory()[1], 4 * limit)
        finally:
            tracemalloc.stop()

    def test_a_stream_that_does_not_inflate_adds_nothing(self):
        # Whatever reads it discards such a chunk: it is counted as stored only.
        broken = chunk(b"zTXt", b"k\0\0not a zlib stream")
        self.assertIsNone(refusal(with_chunks(flat(), broken, chunk(b"iCCP", b"p\0\0\xff\xff"))))

    def test_one_profile_at_most(self):
        self.assertIsNone(refusal(with_chunks(flat(), iccp(3144))))
        self.assertEqual(refusal(with_chunks(flat(), iccp(3144), iccp(3144)))[0], "shape_png")

    def test_every_ancillary_chunk_is_counted_and_bounded_whatever_its_kind(self):
        tiny = chunk(b"prVt", b"x")       # a private chunk no reader knows
        self.assertIsNone(refusal(with_chunks(flat(), *[tiny] * validate.PNG_ANCILLARY_MAX_CHUNKS)))
        self.assertEqual(refusal(with_chunks(flat(), *[tiny] * (validate.PNG_ANCILLARY_MAX_CHUNKS + 1)))[0], "shape_png")
        self.assertIsNone(refusal(with_chunks(flat(), chunk(b"prVt", bytes(validate.PNG_ANCILLARY_MAX_BYTES)))))
        self.assertEqual(refusal(with_chunks(flat(), chunk(b"prVt", bytes(validate.PNG_ANCILLARY_MAX_BYTES + 1))))[0],
                         "shape_png")
        self.assertEqual(refusal(with_chunks(flat(), chunk(b"eXIf", bytes(validate.PNG_ANCILLARY_MAX_BYTES)),
                                             chunk(b"pHYs", bytes(9))))[0], "shape_png")

    def test_the_walk_stops_where_a_reader_would(self):
        # After IEND nothing is read; a file cut short is judged on what it holds.
        image = flat()
        note = chunk(b"tEXt", b"Comment\0x")
        self.assertIsNone(validate.png_ancillary_excess(image + b"".join([note] * 40)))
        cut = with_chunks(image, *[note] * 40)[:33 + 30 * len(note) + 5]
        self.assertIsNotNone(validate.png_ancillary_excess(cut))
        self.assertIsNone(validate.png_ancillary_excess(image[:40]))
        self.assertIsNone(validate.png_ancillary_excess(image[:8]))


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-png-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def package(self, name, document, files):
        folder = self.tmp / name
        for path, data in files.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_bytes(data)
        (folder / "shape.json").write_text(json.dumps(document), encoding="utf-8")
        return shape.check_package(DirectorySource(folder))

    PALETTE = {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E", "plate": "#04283A", "text": "#EFFFFF",
               "accent": "#F2B85C"}

    def v1(self, **blocks):
        return {"format": "mun-shape/1", "palette": self.PALETTE, **blocks}

    def test_every_v1_image_is_held_to_the_bound(self):
        small = flat(64, 36)
        files = {"world/backdrop.png": small, "world/layer.png": small, "world/sprite.png": flat(8, 8),
                 "world/light.png": small, "card/window.png": small}
        document = self.v1(card={"window": "card/window.png"},
                           world={"backdrop": {"image": "world/backdrop.png"},
                                  "layers": [{"image": "world/layer.png"}],
                                  "emitters": [{"sprite": "world/sprite.png", "count": 4, "path": "rise"}],
                                  "light": [{"texture": "world/light.png"}]})
        whole = self.package("whole", document, {name: ordinary(data) for name, data in files.items()})
        self.assertEqual((whole.state, whole.codes()), ("ready", []), "ordinary metadata changes nothing")
        for name in files:
            with self.subTest(name):
                bombed = dict(files, **{name: text_bomb(files[name], shape.IMAGE_MAX_BYTES - 4096)})
                result = self.package(name.replace("/", "-"), document, bombed)
                block = name.split("/")[0]
                self.assertEqual(result.state, "partial")
                self.assertEqual([(n.code, n.block) for n in result.notes], [("shape_png", block)])
                self.assertNotIn(block, result.shape)
                self.assertNotIn(name, result.files, "a refused image is not among the files a console reads")

    def test_every_motion_image_is_held_to_the_bound(self):
        files = {"planes/back.png": flat(640, 360), "sheets/hero.png": flat(192, 48), "lights/rays.png": flat(320, 180),
                 "posters/still.png": flat(192, 108)}
        document = {"format": "mun-shape/1.1", "motion_draft": motion.DRAFT, "palette": self.PALETTE,
                    "world": {"backdrop": {"gradient": ["#02202E", "#2A9DBA"]}},
                    "scene": {"poster": "posters/still.png", "planes": [{"image": "planes/back.png"}]},
                    "actors": {"hero": {"sheet": {"image": "sheets/hero.png"}, "at": [520, 420]}},
                    "lights": {"rays": {"image": "lights/rays.png"}}}
        whole = self.package("whole", document, {name: ordinary(data) for name, data in files.items()})
        self.assertIn("motion", whole.shape, whole.notes)
        for name in files:
            with self.subTest(name):
                bombed = dict(files, **{name: text_bomb(files[name], shape.IMAGE_MAX_BYTES - 4096)})
                result = self.package(name.replace("/", "-"), document, bombed)
                self.assertEqual(result.state, "partial")
                self.assertEqual([(n.code, n.block) for n in result.notes if n.level == "dropped"],
                                 [("shape_png", "motion")])
                self.assertNotIn("motion", result.shape)
                self.assertIn("world", result.shape, "the v1 look stays")

    def test_the_examples_images_are_within_the_bound(self):
        examples = sorted((ROOT / "examples").rglob("*.png"))
        self.assertGreater(len(examples), 30)
        for path in examples:
            with self.subTest(path.relative_to(ROOT).as_posix()):
                data = path.read_bytes()
                self.assertIsNone(validate.png_ancillary_excess(data))
                self.assertIsNone(refusal(data))
        for name in ("sea", "paper", "reef"):
            with self.subTest(name):
                result = shape.check_package(DirectorySource(ROOT / "examples" / "shape" / name))
                self.assertEqual(result.state, "ready", result.notes)
                self.assertNotIn("shape_png", result.codes())


if __name__ == "__main__":
    unittest.main()
