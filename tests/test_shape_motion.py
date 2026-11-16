"""MUN Shape motion (mun-shape/1.1, draft; docs/shape-motion.md): the checker
reads the motion blocks, fills their defaults, resolves their references,
keeps their limits and their budget, and drops the motion as a whole on any
defect while the v1 look stays.

Packages are written to temporary folders with images made here.
"""

import contextlib
import io
import json
import random
import re
import shutil
import struct
import sys
import tempfile
import unittest
import unittest.mock
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import cli, motion, shape, shapetools  # noqa: E402
from mun_card.source import DirectorySource  # noqa: E402


def png(width, height, rgba=(40, 90, 110, 255)):
    return shapetools.encode_png(width, height, [bytes(rgba) * width] * height, alpha=True)


def header_png(width, height, depth=8, colour=6):
    """A PNG the checker accepts, made of its header and a token of data:
    the checker reads headers only, so its size costs nothing here (the
    shell could not decode it)."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0")) + chunk(b"IEND", b""))


BASE = {
    "format": "mun-shape/1.1",
    "motion_draft": motion.DRAFT,
    "palette": {"light": "#F4F1E9", "mid": "#7FA8B8", "deep": "#14232B", "plate": "#1B2E38", "text": "#F4F8FA",
                "accent": "#F2B85C"},
    "card": {"window": "card/window.png", "shape": "card"},
    "world": {"backdrop": {"image": "posters/still.png"}},
    "scene": {"look": "pixel", "poster": "posters/still.png", "planes": [{"image": "backgrounds/sea.png"}]},
    "actors": {"hero": {"sheet": {"image": "actors/hero.png"}, "at": [520, 420]}},
    "paths": {"to-disc": {"points": [[780, 470]]}},
    "states": {"hero.visit": {"path": "to-disc"}},
    "react": {"focus.play": {"hero": "visit"}},
}
FILES = {
    "card/window.png": png(64, 64),
    "posters/still.png": png(192, 108),
    "backgrounds/sea.png": png(640, 360),
    "actors/hero.png": png(48 * 4, 48),
    "actors/turtle.png": png(176 * 6, 176 * 2),
    "effects/rays.png": png(320, 180, (255, 255, 255, 60)),
    "effects/bubble.png": png(4, 4, (230, 250, 255, 200)),
}


class MotionTests(unittest.TestCase):
    def read(self, document, extra=None, compact=False):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name, data in {**FILES, **(extra or {})}.items():
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(data)
            text = json.dumps(document, separators=(",", ":") if compact else None)
            (folder / "shape.json").write_text(text, encoding="utf-8")
            return shape.check_package(DirectorySource(folder))

    def edited(self, **changes):
        document = json.loads(json.dumps(BASE))
        for path, value in changes.items():
            node = document
            keys = path.split("__")
            for key in keys[:-1]:
                node = node[key]
            if value is None:
                del node[keys[-1]]
            else:
                node[keys[-1]] = value
        return document

    def dropped(self, result):
        self.assertEqual(result.state, "partial", result.notes)
        self.assertNotIn("motion", result.shape)
        self.assertIn("palette", result.shape, "the v1 look stays")
        notes = [n for n in result.notes if n.block == "motion" and n.level == "dropped"]
        self.assertEqual(len(notes), 1, notes)
        self.assertNotIn("shape_motion_not_drawn", result.codes(), "nothing is said of a motion not used")
        return notes[0]

    # ------------------------------------------------------------ defaults

    def test_the_template_reads_whole_with_its_defaults_filled_in(self):
        result = self.read(BASE)
        self.assertEqual(result.state, "ready", result.notes)
        # 1.1 is known: no unknown-field notes; the pixel look's colour and
        # dither are read and not drawn yet
        self.assertEqual([(n.code, n.where) for n in result.notes], [("shape_motion_not_drawn", "scene.look")])
        m = result.shape["motion"]
        self.assertEqual(m["look"], {"resolution": [640, 360], "colour": 15, "dither": "bayer4", "sampling": "nearest"})
        self.assertEqual(m["rate"], 20)
        self.assertEqual(m["planes"], [{"image": "backgrounds/sea.png", "z": 0.5, "at": [0.0, 0.0], "drift": {"x": 0.0, "y": 0.0},
                                        "vary": {}}])
        self.assertIsNone(m["depth"], "without depth, z only orders what is drawn")
        self.assertIsNone(m["sway"])
        hero = m["actors"]["hero"]
        self.assertEqual(hero["sheet"], "actor:hero~", "an inline sheet is hoisted under a name of its own")
        sheet = m["sheets"][hero["sheet"]]
        self.assertEqual(sheet["frame"], [48, 48], "square frames of the image's height")
        self.assertEqual(sheet["clips"], {"loop": {"first": 0, "count": 4, "fps": 8.0, "mode": "loop"}})
        self.assertEqual(sheet["views"], 1)
        self.assertEqual((hero["clip"], hero["z"], hero["face"], hero["rest"]), ("loop", 0.5, "fixed", "idle"))
        self.assertIsNone(hero["avoid"])
        self.assertEqual(hero["keep"], "identity", "the character that answers Play")
        self.assertEqual(m["paths"]["to-disc"]["from"], "here")
        self.assertEqual((m["paths"]["to-disc"]["speed"], m["paths"]["to-disc"]["ease"]), (120.0, "inout"))
        self.assertEqual(m["react"]["focus.play"],
                         {"settle": 0.6, "cooldown": 20.0, "once": "per-visit", "do": {"hero": "visit"}})
        self.assertIsNone(m["beats"])

    def test_the_motions_images_are_exported_with_the_package(self):
        result = self.read(BASE)
        for name in ("backgrounds/sea.png", "actors/hero.png", "posters/still.png", "card/window.png"):
            self.assertIn(name, result.files)

    def test_a_native_look_and_an_explicit_resolution(self):
        native = self.read(self.edited(scene__look="native")).shape["motion"]["look"]
        self.assertEqual((native["resolution"], native["sampling"]), (None, "smooth"))
        small = self.read(self.edited(scene__look={"resolution": [960, 540]})).shape["motion"]["look"]
        self.assertEqual(small, {"resolution": [960, 540], "colour": 24, "dither": "none", "sampling": "nearest"})
        self.assertEqual(self.dropped(self.read(self.edited(scene__look={"resolution": [800, 800]}))).code, "shape_range")

    def test_a_10_document_keeps_motion_blocks_unknown(self):
        result = self.read(self.edited(format="mun-shape/1"))
        self.assertNotIn("motion", result.shape)
        unknown = sorted(n.where for n in result.notes if n.code == "shape_unknown_field")
        self.assertEqual(unknown, ["actors", "motion_draft", "paths", "react", "scene", "states"])

    def test_a_later_minor_versions_blocks_are_still_unknown(self):
        document = self.edited(format="mun-shape/1.2")
        document["weather"] = {"rain": True}
        result = self.read(document)
        self.assertIn("motion", result.shape)
        self.assertEqual(sorted(n.code for n in result.notes),
                         ["shape_minor_newer", "shape_motion_not_drawn", "shape_unknown_field"])

    def test_the_motion_is_read_only_for_this_drafts_revision(self):
        self.assertEqual(self.read(BASE).shape["motion"]["draft"], motion.DRAFT)
        for value in (None, motion.DRAFT + 1, motion.DRAFT - 1, str(motion.DRAFT), True, 1.0):
            with self.subTest(value):
                note = self.dropped(self.read(self.edited(motion_draft=value)))
                self.assertEqual((note.code, note.where), ("shape_motion_draft", "motion_draft"))
        # without motion blocks the key reads nothing, and says nothing
        bare = self.edited(scene=None, actors=None, paths=None, states=None, react=None)
        result = self.read(bare)
        self.assertEqual((result.state, result.codes()), ("ready", []))

    # ------------------------------------------------------------ defects

    def test_frames_that_do_not_divide_the_sheet(self):
        document = self.edited(actors__hero__sheet={"image": "actors/turtle.png", "frame": [160, 160]})
        note = self.dropped(self.read(document))
        self.assertEqual(note.code, "shape_motion_field")
        self.assertIn("1056×352", note.detail)
        self.assertEqual(note.where, "actors.hero.sheet.frame")

    def test_a_sheet_over_its_side(self):
        note = self.dropped(self.read(self.edited(actors__hero__sheet={"image": "actors/wide.png"}),
                                      {"actors/wide.png": png(2112, 176)}))
        self.assertEqual(note.code, "shape_png_dimensions")

    def test_references_that_do_not_resolve(self):
        cases = {
            "states__hero.visit": ({"path": "nowhere"}, "states.hero.visit.path"),
            "react": ({"focus.play": {"hero": "dance"}}, "react.focus.play.hero"),
            "actors__hero__rest": ("float", "actors.hero.rest"),
        }
        for path, (value, where) in cases.items():
            with self.subTest(path):
                note = self.dropped(self.read(self.edited(**{path: value})))
                self.assertEqual((note.code, note.where), ("shape_motion_reference", where))

    def test_an_event_mun_does_not_have(self):
        note = self.dropped(self.read(self.edited(react={"focus.gallery": {"hero": "visit"}})))
        self.assertEqual(note.code, "shape_enum")

    def test_limits(self):
        actors = {f"a{i}": {"sheet": {"image": "actors/hero.png"}, "at": [100, 100]} for i in range(motion.MAX_ACTORS + 1)}
        self.assertEqual(self.dropped(self.read(self.edited(actors=actors, states={}, react={}))).code, "shape_motion_count")
        many = [[100 + i, 100] for i in range(motion.MAX_POINTS + 1)]
        self.assertEqual(self.dropped(self.read(self.edited(**{"paths__to-disc__points": many}))).code, "shape_motion_count")
        self.assertEqual(self.dropped(self.read(self.edited(scene__rate=60))).code, "shape_range")

    def test_the_motions_own_budget(self):
        with unittest.mock.patch.object(motion, "MOTION_MAX_BYTES", 1000):
            note = self.dropped(self.read(BASE))
        self.assertEqual(note.code, "shape_motion_budget")

    def test_what_the_console_can_decode(self):
        # The checker reads headers only: images of any size cost nothing
        # here. Four 2048 x 2048 sheets decode to 64 MiB, the budget; a
        # fifth goes over it.
        big = {f"sheets/s{i}.png": header_png(2048, 2048) for i in range(5)}
        sheets = {f"s{i}": {"image": f"sheets/s{i}.png", "frame": [2048, 2048]} for i in range(5)}
        actors = {f"a{i}": {"sheet": f"s{i}", "at": [960, 540]} for i in range(5)}
        four = self.read(self.edited(sheets={k: sheets[k] for k in ("s0", "s1", "s2", "s3")}, scene__planes=[],
                                     actors={k: actors[k] for k in ("a0", "a1", "a2", "a3")}, states={}, react={}), big)
        self.assertIn("motion", four.shape, four.notes)
        self.assertEqual(motion.estimate(four.shape["motion"], four.files)["decoded_bytes"], motion.MOTION_MAX_DECODED)
        with_plane = self.read(self.edited(sheets={k: sheets[k] for k in ("s0", "s1", "s2", "s3")},
                                           actors={k: actors[k] for k in ("a0", "a1", "a2", "a3")}, states={}, react={}), big)
        self.assertEqual(self.dropped(with_plane).code, "shape_motion_budget", "the plane is decoded too")
        five = self.read(self.edited(sheets=sheets, actors=actors, states={}, react={}, scene__planes=[]), big)
        note = self.dropped(five)
        self.assertEqual(note.code, "shape_motion_budget")
        self.assertIn("decodificados", note.detail)
        # one 16-bit image, read by Qt at 8 bytes a pixel, over the shell's
        # limit for one decode (and the same pixels at 8 bits within it)
        for depth, side, ok in ((16, 1448, True), (16, 1449, False), (8, 2048, True)):
            with self.subTest(depth=depth, side=side):
                result = self.read(self.edited(actors__hero__sheet={"image": "actors/deep.png", "frame": [side, side]}),
                                   {"actors/deep.png": header_png(side, side, depth=depth)})
                if ok:
                    self.assertIn("motion", result.shape, result.notes)
                else:
                    note = self.dropped(result)
                    self.assertEqual((note.code, note.where), ("shape_motion_budget", "actors.hero.sheet.image"))
        # the poster is not decoded by the player: not counted
        poster = self.read(self.edited(sheets={k: sheets[k] for k in ("s0", "s1", "s2")},
                                       actors={k: actors[k] for k in ("a0", "a1", "a2")}, states={},
                                       react={}, scene__poster="sheets/s4.png"), big)
        self.assertIn("motion", poster.shape, poster.notes)

    # ------------------------------------------------- what a frame draws

    WALL = {"sheets/wall.png": header_png(2048, 2048), "sheets/screen.png": header_png(1920, 1080),
            "sheets/tile.png": header_png(256, 256)}

    def walls(self, **changes):
        """A native scene of full-screen things only: `planes` planes and
        actors of `copies` figures each, one frame of sheets/screen.png."""
        planes, copies = changes.pop("planes", 0), changes.pop("copies", ())
        actors = {f"wall-{i}": {"sheet": "screen", "at": [960, 540], "copies": {"count": count}}
                  for i, count in enumerate(copies)}
        return self.edited(scene={"look": "native", "planes": [{"image": "sheets/screen.png"}] * planes},
                           sheets={"screen": {"image": "sheets/screen.png", "frame": [1920, 1080]}},
                           actors=actors, paths={}, states={}, react={}, **changes)

    def test_a_motion_within_every_count_that_covers_the_screen_too_often_is_dropped(self):
        # Every limit that counts things is kept: 8 planes, 160 figures in 3
        # actors, one sheet. Each is a 2048 x 2048 frame, the figures at four
        # times their size: 168 things that each cover the whole screen, on
        # every display frame.
        actors = {f"wall-{i}": {"sheet": "wall", "at": [960, 540], "rest": "big", "copies": {"count": count}}
                  for i, count in enumerate((64, 64, 32))}
        document = self.edited(scene={"look": "native", "rate": 30, "planes": [{"image": "sheets/wall.png"}] * 8},
                               sheets={"wall": {"image": "sheets/wall.png", "frame": [2048, 2048]}}, actors=actors,
                               paths={}, states={f"{name}.big": {"vary": {"scale": [4, 4, 600]}} for name in actors},
                               react={})
        self.assertEqual(len(document["scene"]["planes"]), motion.MAX_PLANES)
        self.assertEqual(sum(a["copies"]["count"] for a in actors.values()), motion.MAX_COPIES)
        note = self.dropped(self.read(document, self.WALL))
        self.assertEqual((note.code, note.where), ("shape_motion_count", "motion"))
        self.assertIn("168", note.detail)
        self.assertIn(str(motion.MAX_SCREENS_DRAWN), note.detail)

    def test_the_bound_on_what_a_frame_draws_is_held_at_its_limit(self):
        limit = motion.MAX_SCREENS_DRAWN
        self.assertLessEqual(limit, 32, "far under the 168 screens that held a console at 4 frames a second")
        within = self.read(self.walls(planes=8, copies=(limit - 8,)), self.WALL)
        self.assertIn("motion", within.shape, within.notes)
        self.assertEqual(motion.screens_drawn(within.shape["motion"], within.files), limit)
        over = self.dropped(self.read(self.walls(planes=8, copies=(limit - 8, 1)), self.WALL))
        self.assertEqual((over.code, over.where), ("shape_motion_count", "motion"))

    def test_each_way_a_thing_grows_counts_towards_what_a_frame_draws(self):
        tile = {"tile": {"image": "sheets/tile.png", "frame": [256, 256]}}
        many = {"copies": {"count": 64}}
        cases = {
            # a state's scale: 256 pixels at 4 times, 64 figures, twice
            "scale": self.edited(scene={"look": "native"}, sheets=tile, paths={}, react={},
                                 actors={"a": {"sheet": "tile", "at": [960, 540], "rest": "big", **many},
                                         "b": {"sheet": "tile", "at": [960, 540], "rest": "big", **many}},
                                 states={"a.big": {"vary": {"scale": [4, 4, 600]}}, "b.big": {"vary": {"scale": [1, 4, 9]}}}),
            # perspective: resting at the back, a course to the front, 20 times nearer
            "depth": self.edited(scene={"look": "native", "depth": {"far": 0.05}}, sheets=tile, react={},
                                 actors={"a": {"sheet": "tile", "at": [960, 540], "z": 1, **many}},
                                 paths={"near": {"points": [[960, 540, 0]]}}, states={"a.come": {"path": "near"}}),
            # perspective again: kept away from another actor, in depth too
            "avoid": self.edited(scene={"look": "native", "depth": {"far": 0.05}}, sheets=tile, react={}, paths={}, states={},
                                 actors={"a": {"sheet": "tile", "at": [960, 540], "z": 1, **many,
                                               "avoid": {"actor": "b"}},
                                         "b": {"sheet": "tile", "at": [900, 500], "z": 1}}),
            # particles growing to nine times their frame
            "grow": self.edited(scene={"look": "native"}, sheets=tile, actors={}, paths={}, states={}, react={},
                                emitters={"e": {"sheet": "tile", "at": [960, 540], "max": 320, "grow": 8, "rate": 60}}),
            # a clip that dissolves frame into frame is drawn twice
            "blend": self.edited(scene={"look": "native"}, paths={}, states={}, react={},
                                 sheets={"screen": {"image": "sheets/screen.png", "frame": [1920, 1080],
                                                    "clips": {"loop": [0, 1, 8, "blend"]}}},
                                 actors={"a": {"sheet": "screen", "at": [960, 540], "copies": {"count": 13}}}),
            # a narrow plane or light that drifts sideways is repeated across the screen
            "drift": self.edited(scene={"look": "native", "planes": [{"image": "sheets/tile.png", "drift": {"x": 20}}] * 8},
                                 sheets={"screen": {"image": "sheets/screen.png", "frame": [1920, 1080]}}, paths={},
                                 states={}, react={},
                                 lights={f"l{i}": {"image": "sheets/wall.png", "drift": {"x": 9}} for i in range(4)},
                                 actors={"a": {"sheet": "screen", "at": [960, 540], "copies": {"count": 19}}}),
        }
        for name, document in cases.items():
            with self.subTest(name):
                note = self.dropped(self.read(document, self.WALL))
                self.assertEqual((note.code, note.where), ("shape_motion_count", "motion"), note.detail)
        # The same things at their own size are a scene like any other.
        calm = self.read(self.edited(scene={"look": "native"}, sheets=tile, paths={}, states={}, react={},
                                     actors={"a": {"sheet": "tile", "at": [960, 540], **many},
                                             "b": {"sheet": "tile", "at": [960, 540], **many}},
                                     emitters={"e": {"sheet": "tile", "at": [960, 540], "max": 320, "rate": 60}}),
                         self.WALL)
        self.assertIn("motion", calm.shape, calm.notes)
        self.assertLess(motion.screens_drawn(calm.shape["motion"], calm.files), 15)

    def test_the_bound_is_never_under_what_the_player_draws(self):
        # The player's own arithmetic for one figure (src/motionscene.cpp,
        # collect): its frame at the state's scale, times the perspective of
        # where it is over that of where its place rests, both depths kept
        # within 0 and 1.
        rng = random.Random(1969)

        def depth_scale(z, far):
            return 1 / (1 + z * (1 / far - 1))

        for _ in range(4000):
            far, rest = rng.uniform(0.05, 1), rng.uniform(0, 1)
            nearest, apart = rng.uniform(0, rest), rng.choice((0.0, rng.uniform(0, 1)))
            vary = rng.uniform(0.2, 4)
            document = {"look": {"resolution": None}, "depth": {"far": far, "span": 2000}, "planes": [], "lights": {},
                        "emitters": {}, "sheets": {"s": {"frame": [8, 8], "clips": {"loop": {"mode": "loop"}}}},
                        "paths": {"p": {"points": [[0, 0, nearest], [5, 5]]}},
                        "states": {"a.go": {"vary": {"scale": {"max": vary}}, "path": "p"}},
                        "actors": {"a": {"sheet": "s", "z": rest, "avoid": None,
                                         "copies": {"count": 1, "depth": apart, "places": None}}}}
            bound = motion.screens_drawn(document, {}) * 1920 * 1080
            for _ in range(8):
                where = rng.uniform(nearest, 1)          # anywhere along its course, or deeper
                place = rng.uniform(-apart, apart)
                drawn = vary * depth_scale(min(1, max(0, where + place)), far) / depth_scale(min(1, max(0, rest + place)), far)
                self.assertLessEqual(64 * drawn * drawn, bound * (1 + 1e-9), (far, rest, nearest, apart, where, place))

    def test_the_sample_and_ordinary_scenes_are_far_within_the_bound(self):
        reef = shape.check_package(DirectorySource(REEF))
        self.assertIn("motion", reef.shape, reef.notes)
        drawn = motion.screens_drawn(reef.shape["motion"], reef.files)
        self.assertTrue(4 < drawn < motion.MAX_SCREENS_DRAWN / 2, drawn)
        base = self.read(BASE)
        self.assertLess(motion.screens_drawn(base.shape["motion"], base.files), 2)
        # three planes larger than the screen, as the shell's own checks use
        planes = self.read(self.edited(scene__planes=[{"image": "sheets/wall.png"}] * 3), self.WALL)
        self.assertIn("motion", planes.shape, planes.notes)

    def test_the_exported_document_fits_what_the_shell_reads(self):
        # 16 sheets of 290 clips: 61 KiB of shape.json, within its 64 KiB,
        # but over 256 KiB once every clip is normalised.
        names = [a + b for a in "abcdefghijklmnopqrstuvwxyz" for b in "abcdefghijklmnopqrstuvwxyz"]
        sheets = {f"s{i}": {"image": "effects/bubble.png", "clips": {n: [0, 1, 1] for n in names[:290]}} for i in range(16)}
        document = self.edited(sheets=sheets, actors={"a": {"sheet": "s0", "at": [0, 0]}}, states={}, react={})
        self.assertLess(len(json.dumps(document, separators=(",", ":"))), shape.MANIFEST_MAX_BYTES)
        note = self.dropped(self.read(document, compact=True))
        self.assertEqual((note.code, note.where), ("shape_motion_count", "motion"))
        self.assertIn(str(shape.EXPORT_MAX_BYTES), note.detail)
        reef = shape.check_package(DirectorySource(REEF))
        size = len(json.dumps(reef.shape, ensure_ascii=False).encode("utf-8"))
        self.assertLess(size + shape.EXPORT_RESERVE, shape.EXPORT_MAX_BYTES)

    def test_the_exported_document_is_measured_with_the_cards_version(self):
        # 16 sheets of 268 clips: within the shell's 256 KiB with a short
        # content version, over it with a version of 2,000 digits (the card
        # service adds the version to the document it writes).
        names = [a + b for a in "abcdefghijklmnopqrstuvwxyz" for b in "abcdefghijklmnopqrstuvwxyz"]
        sheets = {f"s{i}": {"image": "effects/bubble.png", "clips": {n: [0, 1, 1] for n in names[:268]}} for i in range(16)}
        document = self.edited(sheets=sheets, actors={"a": {"sheet": "s0", "at": [0, 0]}}, states={}, react={})
        long = "1." + "0" * 2000
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name, data in FILES.items():
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(data)
            (folder / "shape.json").write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
            short = shape.check_package(DirectorySource(folder), version="0.1.0")
            longer = shape.check_package(DirectorySource(folder), version=long)
            huge = shape.check_package(DirectorySource(folder), version="1" * shape.EXPORT_MAX_BYTES)
        self.assertEqual(short.state, "ready", short.notes)
        self.assertLessEqual(len(shape.export_bytes(short.shape, "0" * 16, "0.1.0")), shape.EXPORT_MAX_BYTES)
        note = self.dropped(longer)
        self.assertEqual((note.code, note.where), ("shape_motion_count", "motion"))
        written = shape.export_bytes(longer.shape, "0" * shape.INSERTION_CHARS, long)
        self.assertLessEqual(len(written), shape.EXPORT_MAX_BYTES, "what stays fits, version and all")
        # Even the v1 look does not fit: nothing is used.
        self.assertEqual((huge.state, huge.shape, huge.codes()[-1]), ("unused", None, "shape_export_size"))

    def test_the_service_writes_the_bytes_the_checker_measures(self):
        exported = shape.export_bytes({"format": "mun-shape/1", "palette": {"text": "ñ"}}, "0123456789abcdef", "1.0")
        self.assertEqual(exported, ('{"format": "mun-shape/1", "palette": {"text": "ñ"}, "insertion": "0123456789abcdef", '
                                    '"version": "1.0"}\n').encode("utf-8"))
        service = (ROOT / "services" / "mun-cardd" / "cardd.py").read_text(encoding="utf-8")
        self.assertIn("shape.export_bytes(result.shape, self.insertion, self.version)", service)
        self.assertIn("version=self.version", service)
        self.assertIn("secrets.token_hex(8)", service, f"insertions of {shape.INSERTION_CHARS} characters")

    def test_a_motion_with_nothing_to_draw_is_dropped_and_the_v1_look_kept(self):
        # The player draws the motion in place of the v1 world: a motion of
        # nothing would leave Home over an empty scene.
        for case in ({"scene": {}}, {"scene": {"look": "pixel", "planes": []}, "actors": {}, "emitters": {}, "lights": {}},
                     {"sheets": {"s": {"image": "effects/bubble.png"}}, "paths": {"p": {"points": [[1, 1]]}}}):
            document = {k: v for k, v in BASE.items() if k in ("format", "motion_draft", "palette", "card", "world")}
            document.update(case)
            note = self.dropped(self.read(document))
            self.assertEqual((note.code, note.where), ("shape_motion_empty", "motion"), case)
            self.assertIn("world", self.read(document).shape)

    def test_fields_another_option_leaves_unused_are_refused_where_they_are(self):
        cases = {
            "scene.planes[0].id": self.edited(scene__planes=[{"image": "backgrounds/sea.png", "id": "Not A Name"}]),
            "lights.l.colour": self.edited(lights={"l": {"image": "effects/rays.png", "colour": "#FF0000"}}),
            "lights.l.radius": self.edited(lights={"l": {"image": "effects/rays.png", "radius": "big"}}),
            "paths.to-disc.speed": self.edited(paths={"to-disc": {"points": [[780, 470]], "seconds": 4, "speed": 9}}),
            "paths.to-disc.radius": self.edited(paths={"to-disc": {"points": [[780, 470]], "radius": [10, 10]}}),
            "paths.to-disc.turns": self.edited(paths={"to-disc": {"points": [[780, 470]], "turns": "two"}}),
            # a vary that is not an object, on a thing that bends: said where
            # it is, not taken for the checker's own failure
            "states.hero.visit.vary": self.edited(actors__hero__bend={}, states={"hero.visit": {"path": "to-disc", "vary": 3}}),
        }
        for where, document in cases.items():
            note = self.dropped(self.read(document))
            self.assertEqual((note.code, note.where), ("shape_motion_field", where), where)
        fine = self.edited(scene__planes=[{"image": "backgrounds/sea.png", "id": "back"}],
                           lights={"glow": {"effect": "glow", "at": "disc", "colour": "#9FE7F2", "radius": 300}},
                           paths={"to-disc": {"points": [[780, 470]], "seconds": 4},
                                  "round": {"around": "disc", "radius": [200, 150], "turns": 2, "speed": 60}})
        self.assertIn("motion", self.read(fine).shape, self.read(fine).notes)

    def test_a_drifting_thing_is_wide_enough_to_repeat_a_few_times(self):
        # the pixel look: one image pixel is three canvas pixels
        narrow = self.edited(scene__planes=[{"image": "effects/bubble.png", "drift": {"x": 10}}])
        note = self.dropped(self.read(narrow))
        self.assertEqual((note.code, note.where), ("shape_range", "scene.planes[0].drift"))
        wide = self.edited(scene__planes=[{"image": "effects/strip.png", "drift": {"x": 10}}])
        self.assertIn("motion", self.read(wide, {"effects/strip.png": png(80, 4)}).shape)
        light = self.edited(lights={"l": {"image": "effects/bubble.png", "drift": {"x": -5}}})
        self.assertEqual(self.dropped(self.read(light)).where, "lights.l.drift")
        still = self.edited(scene__planes=[{"image": "effects/bubble.png"}])
        self.assertIn("motion", self.read(still).shape, "only what drifts sideways repeats")

    def test_the_shell_holds_the_same_limits(self):
        source = (ROOT / "services" / "mun-shell" / "src" / "motionscene.cpp").read_text(encoding="utf-8")
        self.assertIn(f"kMotionDecodedMaxBytes = {motion.MOTION_MAX_DECODED // 1048576} * 1024 * 1024", source)
        shell = (ROOT / "services" / "mun-shell" / "src" / "shape.cpp").read_text(encoding="utf-8")
        self.assertIn(f"kDocumentMaxBytes = {shape.EXPORT_MAX_BYTES // 1024} * 1024", shell)
        self.assertIn(f"kAllocationLimitMiB = {motion.DECODE_MAX_BYTES // 1048576};", shell)

    def test_the_shell_reads_a_motions_images_as_every_other_image_of_a_package(self):
        # As PNG and nothing else, and from the header first: the sides
        # within a motion image's and the pixels within what the motion has
        # left to decode, before anything is decoded.
        source = (ROOT / "services" / "mun-shell" / "src" / "motionscene.cpp").read_text(encoding="utf-8")
        sides = {shape.SIDE_LIMITS[role] for role in ("sheet", "plane", "light")}
        self.assertEqual(len(sides), 1)
        self.assertIn(f"kMotionMaxSide = {sides.pop()};", source)
        loader = source[source.index("m_loader = std::thread("):]
        loader = loader[:loader.index("QMetaObject::invokeMethod(")]
        self.assertIn('QImageReader reader(&file, "png");', loader)
        self.assertNotIn("QImageReader reader(path", loader)
        header, pixels = loader.index("reader.size()"), loader.index("reader.read()")
        self.assertLess(header, pixels)
        checked = loader[header:pixels]
        self.assertIn("> kMotionMaxSide", checked)
        self.assertIn("> kMotionDecodedMaxBytes", checked)
        self.assertEqual(checked.count("break;"), 2, "a header over either limit ends the load before decoding")

    def test_the_shell_keeps_a_formations_depth_within_the_scene(self):
        # An actor kept away from another is pushed in depth too, by any
        # amount. Perspective, 1 / (1 + z (1/far - 1)), has a pole in front
        # of the scene (z = -far / (1 - far)): a depth past it turns the
        # scale of the actor's formation negative, its figures thrown
        # across the screen. Each figure's own depth is kept within 0 and
        # 1; so is the depth its formation is spread by.
        far = 0.25
        self.assertLess(1 / (1 + -0.5 * (1 / far - 1)), 0, "past the pole the scale is negative")
        source = (ROOT / "services" / "mun-shell" / "src" / "motionscene.cpp").read_text(encoding="utf-8")
        collect = source[source.index("void MotionScene::collect("):source.index("void MotionScene::onFrame(")]
        self.assertNotIn("depthScale(zc)", collect)
        self.assertIn("const qreal spread = depthScale(std::clamp<qreal>(zc, 0, 1)) / own;", collect)
        for call in re.findall(r"depthScale\(([^()]*(?:\([^()]*\)[^()]*)*)\)", collect):
            self.assertRegex(call, r"^(actor\.z|zm|std::clamp<qreal>\(.*, 0, 1\))$", call)

    def test_unknown_keys_drop_the_motion(self):
        note = self.dropped(self.read(self.edited(actors__hero__speed=3)))
        self.assertEqual((note.code, note.where), ("shape_motion_field", "actors.hero"))

    # ------------------------------------------------------------ states of every kind

    def test_emitters_and_lights_have_states_reactions_switch(self):
        document = self.edited(
            emitters={"bubbles": {"sheet": {"image": "effects/bubble.png"}, "region": [100, 700, 300, 40], "rate": 0.5,
                                  "burst": 6}},
            lights={"rays": {"image": "effects/rays.png", "vary": {"opacity": [0.3, 0.6, 18]}},
                    "glow": {"effect": "glow", "at": "disc", "colour": "#9fe7f2"}},
            states={"hero.visit": {"path": "to-disc"}, "glow.bright": {"opacity": 0.9}, "bubbles.fizz": {"rate": 3}},
            react={"focus.play": {"settle": 0.4, "do": {"hero": "visit", "glow": "bright", "bubbles": "fizz"}},
                   "launch": {"rays": "off", "bubbles": "burst"}},
            beats={"gap": [5, 10], "list": [{"do": {"hero": "visit"}, "every": [20, 40]}]})
        result = self.read(document)
        self.assertEqual(result.state, "ready", result.notes)
        m = result.shape["motion"]
        self.assertEqual(m["states"]["glow.bright"], {"kind": "light", "on": True, "opacity": 0.9, "vary": {}})
        self.assertEqual(m["states"]["bubbles.fizz"]["kind"], "emitter")
        self.assertEqual(m["lights"]["glow"]["colour"], "#9FE7F2")
        self.assertEqual(m["react"]["focus.play"]["settle"], 0.4)
        self.assertEqual(m["react"]["focus.play"]["cooldown"], 20.0, "the defaults stay for what is not said")
        self.assertEqual(m["beats"]["list"][0]["every"], [20.0, 40.0])
        bad = json.loads(json.dumps(document))
        bad["react"]["launch"] = {"rays": "dim"}
        self.assertEqual(self.dropped(self.read(bad)).code, "shape_motion_reference")

    def test_one_name_for_one_thing(self):
        document = self.edited(lights={"hero": {"effect": "glow"}})
        self.assertEqual(self.dropped(self.read(document)).code, "shape_motion_field")

    # ------------------------------------------------------------ water and swimming

    def test_current_turns_bends_and_the_swims_rhythm(self):
        document = self.edited(
            scene__current={"x": 7, "gust": 0.5},
            actors__hero__sheet={"image": "actors/hero.png", "frame": [48, 48],
                                 "clips": {"swim": [0, 4, 8], "glide": [0, 1, 1], "turn": [1, 3, 6, "once"]}},
            actors__hero__turn="turn", actors__hero__bank=0.5,
            paths={"to-disc": {"points": [[780, 470], [900, 400]]}, "straight": {"points": [[100, 100]], "smooth": False}},
            states={"hero.visit": {"path": "to-disc", "surge": 0.3, "glide": {"clip": "glide", "every": [2, 3]}},
                    "hero.rest": {"seconds": 2, "then": "visit"}})
        document["actors"]["kelp"] = {"sheet": {"image": "actors/hero.png", "frame": [48, 48]}, "at": [100, 900],
                                      "bend": {"amount": 10}}
        result = self.read(document)
        self.assertEqual(result.state, "ready", result.notes)
        m = result.shape["motion"]
        self.assertEqual(m["current"], {"x": 7.0, "y": 0.0, "gust": 0.5, "period": 12.0})
        self.assertEqual((m["actors"]["hero"]["turn"], m["actors"]["hero"]["bank"]), ("turn", 0.5))
        self.assertEqual(m["actors"]["kelp"]["bend"], {"amount": 10.0, "period": 5.0, "wave": 0.4, "follow": 0.5})
        self.assertIsNone(m["actors"]["hero"]["bend"])
        self.assertEqual((m["paths"]["to-disc"]["smooth"], m["paths"]["straight"]["smooth"]), (True, False))
        visit = m["states"]["hero.visit"]
        self.assertEqual((visit["surge"], visit["glide"]),
                         ({"amount": 0.3, "at": 0.25}, {"clip": "glide", "every": [2, 3], "seconds": [1.0, 2.0]}))
        timed = self.read(self.edited(**{"states__hero.visit": {"path": "to-disc", "surge": {"amount": 0.4, "at": 0.65}}}))
        self.assertEqual(timed.shape["motion"]["states"]["hero.visit"]["surge"], {"amount": 0.4, "at": 0.65})
        self.assertEqual(m["states"]["hero.rest"]["seconds"], 2.0)

    def test_a_turn_or_a_glide_names_a_clip_of_its_sheet(self):
        note = self.dropped(self.read(self.edited(actors__hero__turn="spin")))
        self.assertEqual((note.code, note.where), ("shape_motion_reference", "actors.hero.turn"))
        note = self.dropped(self.read(self.edited(**{"states__hero.visit": {"path": "to-disc", "glide": {"clip": "rest"}}})))
        self.assertEqual((note.code, note.where), ("shape_motion_reference", "states.hero.visit.glide.clip"))
        self.assertEqual(self.dropped(self.read(self.edited(scene__current={"x": 900}))).code, "shape_range")

    # ------------------------------------------------------------ the authoring tool

    def test_the_card_tool_copies_the_motions_files_from_11_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "package"
            for name, data in {**FILES, "README.md": b"notes"}.items():
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(data)
            for version, expected in (("mun-shape/1.1", True), ("mun-shape/1", False)):
                with self.subTest(version):
                    (folder / "shape.json").write_text(json.dumps({**BASE, "format": version}), encoding="utf-8")
                    staged = Path(tmp) / version.replace("/", "-")
                    shapetools.stage_package(folder, staged)
                    copied = sorted(str(p.relative_to(staged)) for p in staged.rglob("*") if p.is_file())
                    motion_files = {"backgrounds/sea.png", "actors/hero.png"}
                    self.assertEqual(motion_files <= set(copied), expected, copied)
                    self.assertIn("posters/still.png", copied, "v1's world names it too")
                    self.assertNotIn("README.md", copied)
                    self.assertNotIn("actors/turtle.png", copied, "named by nothing")

    # ------------------------------------------------------------ depth

    def test_depth_headings_formations_and_keeping_away(self):
        sheets = {"fish": {"image": "actors/turtle.png", "frame": [176, 176], "views": 2,
                           "clips": {"swim-a": [0, 3, 10], "swim-b": [3, 3, 10, "blend"]}}}
        doc = self.edited(
            scene__depth={"far": 0.25, "span": 3800},
            scene__sway={"x": 60, "y": 20, "period": [56, 69], "pivot": 0.25},
            scene__planes=[{"image": "backgrounds/sea.png", "z": 0.9, "at": [-75, -24]}],
            sheets=sheets,
            actors={"hero": {"sheet": {"image": "actors/hero.png"}, "at": [520, 420]},
                    "school": {"sheet": "fish", "at": [900, 500], "z": 0.3, "face": "views", "bank": 1,
                               "copies": {"places": [[0, 0], [40, -10, 0.05], [-30, 12, -0.04]], "clips": ["swim-a", "swim-b"],
                                          "lag": 0.09, "wobble": [8, 4.8]},
                               "avoid": {"actor": "hero", "radius": 400, "push": 200}, "rest": "round"}},
            paths={"to-disc": {"points": [[780, 470]]},
                   "round": {"points": [[900, 500, 0.3], [1200, 520, 0.5], [900, 540, 0.7], [600, 520, 0.5]],
                             "seconds": 60, "loop": True, "pace": "even"}},
            states={"hero.visit": {"path": "to-disc"}, "school.round": {"path": "round"},
                    "school.fade": {"vary": {"y": [40, -40, 25, "saw", 0.3], "opacity": [0, 1, 25, "fade", 0.3]}}},
        )
        result = self.read(doc)
        self.assertEqual(result.state, "ready", result.notes)
        m = result.shape["motion"]
        self.assertEqual(m["depth"], {"far": 0.25, "span": 3800.0})
        self.assertEqual(m["sway"], {"x": 60.0, "y": 20.0, "period": [56.0, 69.0], "pivot": 0.25})
        self.assertEqual(m["planes"][0]["at"], [-75.0, -24.0])
        self.assertEqual(m["sheets"]["fish"]["views"], 2)
        self.assertEqual(m["sheets"]["fish"]["frames"], 6, "with views, clips name the columns of a row")
        self.assertEqual(m["sheets"]["fish"]["clips"]["swim-b"]["mode"], "blend")
        school = m["actors"]["school"]
        self.assertEqual(school["face"], "views")
        self.assertEqual(school["copies"]["count"], 3, "one figure for each place")
        self.assertEqual(school["copies"]["places"][0], [0.0, 0.0, 0.0])
        self.assertEqual(school["copies"]["clips"], ["swim-a", "swim-b"])
        self.assertEqual(school["avoid"], {"actor": "hero", "radius": 400.0, "push": 200.0})
        self.assertEqual(m["paths"]["round"]["pace"], "even")
        self.assertEqual(m["paths"]["round"]["points"][1], [1200.0, 520.0, 0.5])
        self.assertEqual(m["states"]["school.fade"]["vary"]["opacity"], {"min": 0.0, "max": 1.0, "period": 25.0,
                                                                         "shape": "fade", "phase": 0.3})

    def test_depth_mistakes_drop_the_motion(self):
        fish = {"image": "actors/turtle.png", "frame": [176, 176], "views": 2, "clips": {"swim": [0, 3, 10]}}
        cases = {
            "rows that are not the views": dict(sheets={"fish": dict(fish, views=3)}),
            "views without a sheet of views": dict(actors={"hero": {"sheet": {"image": "actors/hero.png"}, "at": [520, 420],
                                                                     "face": "views"}}),
            "a clip past a row's columns": dict(sheets={"fish": dict(fish, clips={"swim": [4, 3, 10]})}),
            "keeping away from itself": dict(actors={"hero": {"sheet": {"image": "actors/hero.png"}, "at": [520, 420],
                                                              "avoid": {"actor": "hero"}}}),
            "a copy's clip not on its sheet": dict(actors={"hero": {"sheet": {"image": "actors/hero.png"}, "at": [520, 420],
                                                                    "copies": {"places": [[0, 0]], "clips": ["swim"]}}}),
            "places and a count that disagree": dict(actors={"hero": {"sheet": {"image": "actors/hero.png"}, "at": [520, 420],
                                                                      "copies": {"count": 3, "places": [[0, 0]]}}}),
            "a pace of its own": dict(paths={"to-disc": {"points": [[780, 470]], "pace": "fast"}}),
            "too far a depth": dict(scene__depth={"far": 0}),
            "a sway past its range": dict(scene__sway={"x": 900}),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.dropped(self.read(self.edited(**change)))

    def test_the_reef_template_reads_whole(self):
        # examples/shape/reef, made by its studio (examples/shape/reef-studio)
        result = shape.check_package(DirectorySource(ROOT / "examples" / "shape" / "reef"))
        self.assertEqual(result.state, "ready", result.notes)
        # what it uses that the player does not draw yet, said once a class
        self.assertEqual([(n.code, n.where) for n in result.notes],
                         [("shape_motion_not_drawn", "scene.look"), ("shape_motion_not_drawn", "actors.light-far.keep"),
                          ("shape_motion_not_drawn", "lights.glow.colour")])
        m = result.shape["motion"]
        self.assertIsNotNone(m["depth"])
        self.assertIsNotNone(m["sway"])
        self.assertEqual(m["actors"]["fish-near"]["keep"], "identity", "the school that answers Play")
        self.assertTrue(all(a["face"] == "views" for n, a in m["actors"].items() if n.startswith("fish-") or n == "shark"))

    def test_a_paths_points_measured_from_the_disc(self):
        loop = {"points": [[-230, 290, 0.16], [0, 350], [230, 290, 0.16]], "origin": "disc", "loop": True}
        result = self.read(self.edited(paths={"to-disc": {"points": [[780, 470]]}, "round": loop}))
        self.assertIn("motion", result.shape, result.notes)
        paths = result.shape["motion"]["paths"]
        self.assertEqual(paths["round"]["origin"], "disc")
        self.assertEqual(paths["round"]["points"][1], [0.0, 350.0], "a point without depth keeps the actor's")
        self.assertEqual(paths["to-disc"]["origin"], "canvas", "by default, the canvas's top left")
        cases = {
            "an offset past the canvas": (dict(loop, points=[[-2000, 0]]), "shape_range"),
            "an anchor MUN does not have": (dict(loop, origin="menu"), "shape_enum"),
            "nothing to measure": ({"to": "disc", "origin": "disc"}, "shape_motion_field"),
        }
        for name, (path, code) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.dropped(self.read(self.edited(paths={"to-disc": {"points": [[780, 470]]},
                                                                           "round": path}))).code, code)
        reef = shape.check_package(DirectorySource(REEF)).shape["motion"]["paths"]["gather"]
        self.assertEqual(reef["origin"], "disc", "the reef's school mills round the card object, wherever it is")

    def test_the_disc_pulls_particles_in(self):
        doc = self.edited(emitters={"snow": {"sheet": {"image": "effects/bubble.png"}, "region": [0, 0, 1920, 1080], "max": 40,
                                             "pull": {"radius": 700, "speed": 300, "swirl": 0.5, "swallow": 60}}})
        result = self.read(doc)
        self.assertEqual(result.state, "ready", result.notes)
        self.assertEqual(result.shape["motion"]["emitters"]["snow"]["pull"],
                         {"radius": 700.0, "speed": 300.0, "swirl": 0.5, "swallow": 60.0})

    # ------------------------------------------------------------ the estimate

    def test_the_estimate_counts_what_a_frame_draws(self):
        result = self.read(BASE)
        cost = motion.estimate(result.shape["motion"], result.files)
        self.assertEqual(cost["resolution"], [640, 360])
        self.assertEqual(cost["drawn_per_frame"], 640 * 360 + 48 * 48)
        # drawn on every display frame (60 a second), simulated at 20 steps
        self.assertEqual(cost["drawn_per_second"], (640 * 360 + 48 * 48) * 60)
        self.assertEqual((cost["display_rate"], cost["steps_per_second"]), (60, 20))
        self.assertEqual(cost["decoded_bytes"], (640 * 360 + 192 * 48) * 4)
        self.assertEqual(cost["keep"]["identity"], ["hero"])


class NotDrawnTests(unittest.TestCase):
    """What the checker reads and the player does not draw yet is said, in
    the checker, with a note (shape_motion_not_drawn, information only: the
    motion is used); what makes no sense to the player is refused."""

    read = MotionTests.read
    edited = MotionTests.edited
    dropped = MotionTests.dropped

    def said(self, result):
        self.assertIn("motion", result.shape, result.notes)
        return [n.where for n in result.notes if n.code == "shape_motion_not_drawn"]

    def test_each_option_read_and_not_drawn_is_said(self):
        bend = {"sheet": {"image": "actors/hero.png", "frame": [48, 48], "clips": {"a": [0, 2, 4, "blend"]}},
                "at": [100, 900], "bend": {"amount": 10}}
        glow = {"effect": "glow", "at": "disc"}
        light = {"image": "effects/rays.png"}
        cases = {
            "the pixel look's colour and dither": ({}, "scene.look"),
            "a screen blend": ({"lights": {"l": dict(light, blend="screen")}}, "lights.l.blend"),
            "an add blend": ({"lights": {"l": dict(light, blend="add")}}, "lights.l.blend"),
            "a plane turning": ({"scene__planes": [{"image": "backgrounds/sea.png", "vary": {"rotate": [-5, 5, 9]}}]},
                                "scene.planes[0].vary.rotate"),
            "a light growing": ({"lights": {"l": dict(light, vary={"scale": [1, 2, 9]})}}, "lights.l.vary.scale"),
            "a bending actor's blend clip": ({"actors__kelp": bend}, "actors.kelp.bend"),
            "a bending actor turning": ({"actors__kelp": dict(bend, sheet={"image": "actors/hero.png", "frame": [48, 48]},
                                                              rest="sway"),
                                         "states__kelp.sway": {"vary": {"rotate": [-9, 9, 4]}}}, "states.kelp.sway.vary.rotate"),
            "a glow's colour": ({"lights": {"g": dict(glow, colour="#9FE7F2")}}, "lights.g.colour"),
            "a glow drifting": ({"lights": {"g": dict(glow, drift={"x": 4})}}, "lights.g.drift"),
            "a glow varying its place": ({"lights": {"g": dict(glow, vary={"y": [-4, 4, 3]})}}, "lights.g.vary.y"),
            "keep": ({"actors__hero__keep": "ambient"}, "actors.hero.keep"),
            "once per insertion": ({"react": {"focus.play": {"once": "per-insertion", "do": {"hero": "visit"}}}},
                                   "react.focus.play.once"),
            "a beat's longest wait": ({"beats": {"list": [{"do": {"hero": "visit"}, "every": [8, 24]}]}},
                                      "beats.list[0].every"),
            "a light state's variation": ({"lights": {"g": glow}, "states__g.dim": {"opacity": 0.2, "vary": {"opacity": [0, 1, 4]}}},
                                          "states.g.dim.vary"),
            "a poster that is not the backdrop": ({"scene__poster": "backgrounds/sea.png"}, "scene.poster"),
            "an emitter toward the disc with a velocity": (
                {"emitters": {"e": {"sheet": {"image": "effects/bubble.png"}, "at": [0, 0], "toward": "disc",
                                    "velocity": [0, -50]}}}, "emitters.e.velocity"),
            "an emitter's clip played once": (
                {"emitters": {"e": {"sheet": {"image": "effects/bubble.png", "clips": {"pop": [0, 1, 8, "once"]}},
                                    "at": [0, 0]}}}, "emitters.e.clip"),
        }
        for name, (change, where) in cases.items():
            with self.subTest(name):
                document = self.edited(**({"scene__look": "native"} if name != "the pixel look's colour and dither" else {}),
                                       **change)
                self.assertEqual(self.said(self.read(document)), [where])

    def test_what_is_drawn_says_nothing(self):
        document = self.edited(scene__look="native",
                               lights={"g": {"effect": "glow", "at": "disc"}, "l": {"image": "effects/rays.png"}},
                               beats={"list": [{"do": {"hero": "visit"}, "every": [10, 10]}]},
                               react={"focus.play": {"once": "per-visit", "do": {"hero": "visit"}}})
        self.assertEqual(self.said(self.read(document)), [])

    def test_a_class_is_said_once_with_its_places(self):
        lights = {f"l{i}": {"image": "effects/rays.png", "blend": "add"} for i in range(4)}
        result = self.read(self.edited(scene__look="native", lights=lights))
        notes = [n for n in result.notes if n.code == "shape_motion_not_drawn"]
        self.assertEqual(len(notes), 1)
        self.assertIn("lights.l0.blend, lights.l1.blend, lights.l2.blend y 1 más", notes[0].detail)

    def test_what_makes_no_sense_to_the_player_is_refused(self):
        bubble = {"sheet": {"image": "effects/bubble.png"}, "at": [0, 0]}
        cases = {
            "a glow with nowhere to be": ({"lights": {"g": {"effect": "glow"}}}, "lights.g.at"),
            "a settle on an event applied at once": ({"react": {"arrive": {"settle": 2, "do": {"hero": "visit"}}}},
                                                     "react.arrive"),
            "once on focus.away": ({"react": {"focus.away": {"once": "per-visit", "do": {"hero": "idle"}}}},
                                   "react.focus.away"),
            "a burst of no particles": ({"emitters": {"e": bubble}, "react": {"launch": {"e": "burst"}}}, "react.launch.e"),
            "two sources": ({"emitters": {"e": dict(bubble, region=[0, 0, 10, 10])}}, "emitters.e"),
            "a fraction of strokes": ({"states__hero.visit": {"path": "to-disc", "glide": {"clip": "loop", "every": [1.5, 3]}}},
                                      "states.hero.visit.glide.every[0]"),
        }
        for name, (change, where) in cases.items():
            with self.subTest(name):
                note = self.dropped(self.read(self.edited(**change)))
                self.assertEqual((note.code, note.where), ("shape_motion_field", where))
        counted = self.read(self.edited(emitters={"e": dict(bubble, burst=4)}, react={"launch": {"e": "burst"}}))
        self.assertIn("motion", counted.shape, counted.notes)

    def test_the_cards_summary_says_what_becomes_of_the_motion(self):
        used = cli.shape_summary(self.read(BASE))
        self.assertEqual(used["blocks"]["motion"], {"state": "used", "draft": motion.DRAFT})
        dropped = cli.shape_summary(self.read(self.edited(motion_draft=None)))
        self.assertEqual((dropped["blocks"]["motion"]["state"], dropped["blocks"]["motion"]["code"]),
                         ("dropped", "shape_motion_draft"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._print_shape_summary(dropped)
        self.assertIn("descartado movimiento [shape_motion_draft]", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._print_shape_summary(used)
        self.assertIn("movimiento", out.getvalue().splitlines()[1], "among what is used")

    def test_the_report_says_keep_does_nothing_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(REEF, Path(tmp) / "reef")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["shape", "check", str(Path(tmp) / "reef"), "--report"])
        text = out.getvalue()
        self.assertNotIn("se quitan antes", text)
        self.assertIn("keep, sin efecto todavía", text)
        self.assertIn("shape_motion_not_drawn", text)


REEF = ROOT / "examples" / "shape" / "reef"
# The codes a defect of the package's motion is named by: a precise one,
# never the checker's own failure (shape_motion_error).
MOTION_DEFECTS = {"shape_motion_field", "shape_motion_reference", "shape_motion_count", "shape_motion_budget",
                  "shape_motion_draft", "shape_range", "shape_enum", "shape_colour", "shape_path_unsafe",
                  "shape_path_missing", "shape_path_symlink", "shape_path_type", "shape_file_too_large", "shape_png",
                  "shape_png_dimensions"}


def _set(*path, value):
    def edit(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return edit


# Fields of the wrong type, in the reef template: each must drop the motion
# with a note and keep the v1 look; none may stop the checker.
MISTYPED = {
    "a state's path as a list": _set("states", "fish-near.gather", "path", value=["gather"]),
    "a state's path as an object": _set("states", "fish-near.gather", "path", value={}),
    "a state's clip as a list": _set("states", "fish-near.gather", "clip", value=["swim-a"]),
    "a state's then as a number": _set("states", "fish-near.dive", "then", value=5),
    "a state's then as a list": _set("states", "fish-near.dive", "then", value=["gone"]),
    "an actor's clip as a number": _set("actors", "crab-0", "clip", value=3),
    "an actor's clip as a list": _set("actors", "crab-0", "clip", value=["loop"]),
    "an actor's sheet as a number": _set("actors", "crab-0", "sheet", value=3),
    "an actor's sheet as a list": _set("actors", "crab-0", "sheet", value=["crab-0"]),
    "an actor as a list": _set("actors", "crab-0", value=[]),
    "an actor as null": _set("actors", "crab-0", value=None),
    "a light's at as an object": _set("lights", "glow", "at", value={"x": 1}),
    "a light's at as a list of lists": _set("lights", "glow", "at", value=[[1, 2], [3, 4]]),
    "a fractional count of copies": _set("actors", "crab-0", "copies", value={"count": 2.5}),
    "a count as text beside places": _set("actors", "jellies", "copies", "count", value="6"),
    "a copy's clips nested": _set("actors", "jellies", "copies", "clips", value=[["pulse-0"]]),
    "copies as a number": _set("actors", "jellies", "copies", value=3),
    "a state as a list": _set("states", "crab-0.walk", value=[]),
    "a state of a thing named by a number": _set("states", "5.x", value={}),
    "actors as null": _set("actors", value=None),
    "the scene as null": _set("scene", value=None),
    "the scene as a list": _set("scene", value=[]),
    "a plane as text": _set("scene", "planes", value=["planes/plane-back.png"]),
    "a plane's image as a number": _set("scene", "planes", value=[{"image": 5}]),
    "the poster as a number": _set("scene", "poster", value=5),
    "the look as a number": _set("scene", "look", value=5),
    "the look as a list": _set("scene", "look", value=[640, 360]),
    "the look's colour as a list": _set("scene", "look", value={"resolution": [640, 360], "colour": [15]}),
    "the look's colour as text": _set("scene", "look", value={"resolution": [640, 360], "colour": "15"}),
    "a clip as an object": _set("sheets", "bubble", "clips", value={"a": {"first": 0}}),
    "a frame of nothing": _set("sheets", "bubble", "frame", value=[0, 0]),
    "a sheet's image as a list": _set("sheets", "bubble", "image", value=["x"]),
    "a sheet as text": _set("sheets", "bubble", value="sheets/bubble.png"),
    "an emitter's sheet as a list": _set("emitters", "bubbles", "sheet", value=["bubble"]),
    "an emitter's clip as a list": _set("emitters", "bubbles", "clip", value=["loop"]),
    "a ring of the wrong kinds": _set("emitters", "bubbles", "ring", value=["disc", 5]),
    "a pull as a list": _set("emitters", "bubbles", "pull", value=[1]),
    "toward as a list": _set("emitters", "bubbles", "toward", value=["disc"]),
    "avoiding a list": _set("actors", "fish-near", "avoid", value={"actor": ["shark"]}),
    "avoiding an object": _set("actors", "fish-near", "avoid", value={"actor": {}}),
    "a glide's clip as a list": _set("states", "fish-near.gather", "glide", value={"clip": ["swim-a"]}),
    "a surge as a list": _set("states", "fish-near.gather", "surge", value=[0.5]),
    "beats as a list": _set("beats", value=[]),
    "a beat's target as a list": _set("beats", value={"list": [{"do": {"bubbles": ["burst"]}}]}),
    "a beat as text": _set("beats", value={"list": ["x"]}),
    "a reaction as a list": _set("react", "arrive", value=["x"]),
    "a reaction's state as an object": _set("react", "arrive", value={"bubbles": {}}),
    "a reaction's do as a list": _set("react", "focus.play", value={"do": ["x"]}),
    "a path's seconds as text": _set("paths", "dive", "seconds", value="1"),
    "around a list": _set("paths", "dive", value={"around": ["disc"]}),
    "to a list": _set("paths", "dive", "to", value=["disc"]),
    "from a list": _set("paths", "dive", "from", value=["disc"]),
    "a point as an object": _set("paths", "gather", "points", value=[{"x": 1}]),
    "a curve's shape as a number": _set("states", "crab-0.walk", "vary", value={"x": [0, 1, 1, 5]}),
    "a curve as an object": _set("states", "crab-0.walk", "vary", value={"x": {"min": 0}}),
    "a light's effect as a list": _set("lights", "glow", "effect", value=["glow"]),
    "a light's colour as a number": _set("lights", "glow", "colour", value=5),
    "a light as a list": _set("lights", "glow", value=[]),
    "an emitter as a list": _set("emitters", "bubbles", value=[]),
    "a rest as a number": _set("actors", "crab-0", "rest", value=5),
    "a turn as a list": _set("actors", "crab-0", "turn", value=["loop"]),
    "a face as a list": _set("actors", "crab-0", "face", value=["fixed"]),
    "a keep as a list": _set("actors", "crab-0", "keep", value=["detail"]),
    "a path as a list": _set("paths", "dive", value=[]),
    "sheets as a list": _set("sheets", value=[]),
    "reactions as a list": _set("react", value=[]),
}


class MistypedTests(unittest.TestCase):
    """A field of the wrong type anywhere in the motion is the package's
    defect: the motion is dropped with a note that names it, the v1 look
    stays, and the checker never stops (the card service runs it on the
    console, and a stopped checker would lose the whole package)."""

    def read(self, edit):
        document = json.loads((REEF / "shape.json").read_text(encoding="utf-8"))
        edit(document)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "reef"
            shutil.copytree(REEF, folder)
            (folder / "shape.json").write_text(json.dumps(document), encoding="utf-8")
            return shape.check_package(DirectorySource(folder))

    def test_every_mistyped_field_drops_only_the_motion(self):
        for label, edit in MISTYPED.items():
            with self.subTest(label):
                try:
                    result = self.read(edit)
                except Exception as exc:     # the defect this guards against
                    self.fail(f"the checker stopped: {type(exc).__name__}: {exc}")
                self.assertEqual(result.state, "partial", result.notes)
                self.assertNotIn("motion", result.shape)
                for block in ("palette", "card", "world", "transition", "sounds"):
                    self.assertIn(block, result.shape, "the v1 look stays")
                notes = [n for n in result.notes if n.block == "motion" and n.level == "dropped"]
                self.assertEqual(len(notes), 1, result.notes)
                self.assertIn(notes[0].code, MOTION_DEFECTS, notes[0])
                self.assertTrue(notes[0].where, "the note says where")

    def test_a_failure_of_the_checkers_own_keeps_the_v1_look_and_says_why(self):
        def broken(*args, **kwargs):
            raise RuntimeError("an example")
        with unittest.mock.patch.object(motion, "check", broken):
            result = self.read(lambda document: None)
        self.assertEqual(result.state, "partial")
        self.assertNotIn("motion", result.shape)
        self.assertIn("world", result.shape)
        note = next(n for n in result.notes if n.block == "motion")
        self.assertEqual((note.code, note.level), ("shape_motion_error", "dropped"))
        self.assertIn("RuntimeError: an example", note.detail)


if __name__ == "__main__":
    unittest.main()
