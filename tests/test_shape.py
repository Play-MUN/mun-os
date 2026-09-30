"""Host-side tests for MUN Shape packages: the strict JSON profile, the
schema, file checks, budgets, contrast proofs, the read level, the template,
the defective fixtures and the two sample packages (docs/shape.md)."""

import contextlib
import importlib.util
import io
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import cli, image, shape, shapetools  # noqa: E402
from mun_card.source import DebugfsSource, DirectorySource  # noqa: E402
from mun_card.validate import validate_card  # noqa: E402

SAMPLES = ROOT / "examples" / "shape"


def have_e2fsprogs() -> bool:
    try:
        image.find_tool("mke2fs")
        image.find_tool("debugfs")
        return True
    except Exception:
        return False


def unusable(raw: bytes) -> str:
    try:
        shape.parse_manifest(raw)
    except shape._Unusable as exc:
        return exc.code
    return "parsed"


def png(width, height, pixels, colour=6, depth=8, interlace=0, palette=b"", transparency=b""):
    """A PNG of any colour type and depth from raw sample rows (filter 0),
    with Adam7 when asked: for the decoder's tests."""
    channels = shapetools._CHANNELS[colour]

    def packed(row):
        if depth == 8:
            return bytes(row)
        if depth == 16:
            return b"".join(struct.pack(">H", v) for v in row)
        out, per = bytearray(), 8 // depth
        for i in range(0, len(row), per):
            byte = 0
            for j, v in enumerate(row[i:i + per]):
                byte |= v << (8 - depth * (j + 1))
            out.append(byte)
        return bytes(out)

    raw = bytearray()
    passes = shapetools._ADAM7 if interlace else ((0, 0, 1, 1),)
    for x0, y0, dx, dy in passes:
        for y in range(y0, height, dy):
            row = []
            for x in range(x0, width, dx):
                row.extend(pixels[y][x * channels:(x + 1) * channels])
            if row:
                raw.append(0)
                raw += packed(row)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    data = shape._PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace))
    if palette:
        data += chunk(b"PLTE", palette)
    if transparency:
        data += chunk(b"tRNS", transparency)
    return data + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


class StrictJsonTests(unittest.TestCase):
    def test_a_plain_document_parses(self):
        self.assertEqual(shape.parse_manifest(b'{"format": "mun-shape/1", "a": [1, 2.5, true, null]}')["a"],
                         [1, 2.5, True, None])

    def test_each_rule_has_its_code(self):
        cases = {
            b'{"a": 1, "a": 2}': "shape_duplicate_key",
            b'{"x": {"b": 1, "b": 2}}': "shape_duplicate_key",
            b'{"a": NaN}': "shape_number",
            b'{"a": -Infinity}': "shape_number",
            b'{"a": 1e999}': "shape_number",
            b'{"a": ' + b"1" * 40 + b"}": "shape_number",
            b'[1]': "shape_structure",
            b'"text"': "shape_structure",
            b'{"a": }': "shape_syntax",
            b'{"a": "tab\tinside"}': "shape_syntax",
            b"\xef\xbb\xbf{}": "shape_encoding",
            b'{"a": "\xff"}': "shape_encoding",
            b" " * (shape.MANIFEST_MAX_BYTES + 1): "shape_too_large",
        }
        for raw, code in cases.items():
            self.assertEqual(unusable(raw), code, raw[:40])

    def test_depth_six_is_allowed_and_seven_refused_before_parsing(self):
        six = b'{"a": [[[[[1]]]]]}'
        seven = b'{"a": [[[[[[1]]]]]]}'
        self.assertEqual(unusable(six), "parsed")
        self.assertEqual(unusable(seven), "shape_depth")
        # Brackets inside strings do not count; a document deep enough to
        # exhaust the parser's recursion is refused by the scan first.
        self.assertEqual(unusable(b'{"a": "[[[[[[[[[["}'), "parsed")
        self.assertEqual(unusable(b"[" * 50000 + b"]" * 50000), "shape_too_large")
        self.assertEqual(unusable(b"[" * 30000 + b"]" * 30000), "shape_depth")

    def test_strings_and_keys_are_bounded(self):
        self.assertEqual(unusable(json.dumps({"a": "x" * 512}).encode()), "parsed")
        self.assertEqual(unusable(json.dumps({"a": "x" * 513}).encode()), "shape_string_too_long")
        self.assertEqual(unusable(json.dumps({"k" * 513: 1}).encode()), "shape_string_too_long")
        self.assertEqual(unusable(json.dumps({"a": [{"b": "x" * 600}]}).encode()), "shape_string_too_long")

    def test_errors_carry_positions(self):
        with self.assertRaises(shape._Unusable) as caught:
            shape.parse_manifest(b'{\n  "a": 1,\n  "b": {"c": 1, "c": 2}\n}')
        self.assertEqual(caught.exception.where, "línea 3, columna 8")
        with self.assertRaises(shape._Unusable) as caught:
            shape.parse_manifest(b'{\n  "a": 1,\n  "b": ]\n}')
        self.assertEqual(caught.exception.where, "línea 3, columna 8")

    def test_format_versions(self):
        self.assertEqual(shape.format_version({"format": "mun-shape/1"}), (1, 0))
        self.assertEqual(shape.format_version({"format": "mun-shape/1.3"}), (1, 3))
        for document, code in (({}, "shape_format"), ({"format": 1}, "shape_format"),
                               ({"format": "mun-shape/one"}, "shape_format"),
                               ({"format": "mun-shape/2"}, "shape_format_unsupported"),
                               ({"format": "mun-shape/0.9"}, "shape_format_unsupported")):
            with self.assertRaises(shape._Unusable) as caught:
                shape.format_version(document)
            self.assertEqual(caught.exception.code, code, document)


class FileStructureTests(unittest.TestCase):
    def test_png_structure(self):
        good = shapetools.encode_png(4, 3, [bytes(16)] * 3, alpha=True)
        self.assertEqual(shape.png_header(good)["width"], 4)
        bad = {
            "signature": b"GIF89a" + good[6:],
            "crc": good[:-1] + bytes([good[-1] ^ 1]),
            "trailing": good + b"\0",
            "truncated": good[:-6],
            "ihdr-late": good[:8] + good[33:],
            "depth": good[:24] + bytes([7]) + good[25:],
            "not-zlib": None,
        }
        # The image data not starting as a zlib stream (CRC kept valid).
        start = 33
        length = struct.unpack(">I", good[start:start + 4])[0]
        body = b"\x00\x00" + good[start + 10:start + 8 + length]
        chunk = struct.pack(">I", len(body)) + b"IDAT" + body + struct.pack(">I", zlib.crc32(b"IDAT" + body) & 0xFFFFFFFF)
        bad["not-zlib"] = good[:start] + chunk + good[start + 12 + length:]
        for name, data in bad.items():
            with self.assertRaises(shape._Invalid, msg=name) as caught:
                shape.png_header(data)
            self.assertEqual(caught.exception.code, "shape_png", name)

    def test_critical_chunks_and_palettes(self):
        pixels = [[0, 1, 1, 0]] * 2
        indexed = png(4, 2, pixels, colour=3, palette=b"\0\0\0\xff\xff\xff")
        self.assertEqual(shape.png_header(indexed)["colour"], 3)
        without_palette = png(4, 2, pixels, colour=3)
        with self.assertRaises(shape._Invalid):
            shape.png_header(without_palette)
        grey_with_palette = png(4, 2, pixels, colour=0, palette=b"\0\0\0")
        with self.assertRaises(shape._Invalid):
            shape.png_header(grey_with_palette)

    def test_wav_format(self):
        good = shapetools.encode_wav([(1000, -2000)] * 4800)
        info = shape.wav_header(good)
        self.assertAlmostEqual(info["seconds"], 0.1)
        self.assertAlmostEqual(info["peak_dbfs"], 20 * __import__("math").log10(2000 / 32768))
        for name, data in {
            "riff-size": good[:4] + struct.pack("<I", 1) + good[8:],
            "odd-data": good[:40] + struct.pack("<I", 4799 * 4 + 2) + good[44:-2],
            "empty": good[:40] + struct.pack("<I", 0) + b"",
            "list-chunk": shapetools._wav_with_list(good),
            "mono": shapetools._mono_wav(),
        }.items():
            if name == "empty":
                data = good[:4] + struct.pack("<I", 36) + good[8:40] + struct.pack("<I", 0)
            with self.assertRaises(shape._Invalid, msg=name):
                shape.wav_header(data)


class ContrastTests(unittest.TestCase):
    def test_mun_own_set_holds_on_every_material(self):
        for material in shape.MATERIALS + ("plain",):
            self.assertIsNotNone(shape.minimum_opacity(shape.NEUTRAL, material), material)
        opacities, reason = shape.check_set(shape.NEUTRAL, {"entries": "glass", "panel": "paper", "bands": "glass"})
        self.assertIsNotNone(opacities, reason)
        self.assertGreaterEqual(shape.contrast_ratio(shape.NEUTRAL["bar_text"], shape.NEUTRAL["bar"]), shape.TEXT_RATIO)

    def test_ratio_matches_wcag(self):
        self.assertAlmostEqual(shape.contrast_ratio("#000000", "#FFFFFF"), 21.0, places=6)
        self.assertAlmostEqual(shape.contrast_ratio("#777777", "#FFFFFF"), 4.48, places=2)

    def test_bounds_contain_every_composite(self):
        """The proof's bounds hold for sampled worlds, opacities above the
        floor and plate colours between the vertices."""
        rng = random.Random(5)
        for _ in range(200):
            plates = [tuple(rng.random() for _ in range(3)) for _ in range(rng.randint(1, 3))]
            floor = rng.random()
            low, high = shape.composite_bounds(floor, plates)
            for _ in range(20):
                alpha = floor + (1 - floor) * rng.random()
                mix = [rng.random() for _ in plates]
                total = sum(mix)
                plate = tuple(sum(p[i] * m for p, m in zip(plates, mix)) / total for i in range(3))
                world = tuple(rng.random() for _ in range(3))
                composite = tuple(alpha * p + (1 - alpha) * w for p, w in zip(plate, world))
                value = shape.luminance(composite)
                self.assertLessEqual(low, value + 1e-12)
                self.assertGreaterEqual(high, value - 1e-12)

    def test_opacity_is_the_least_that_holds_over_black_and_white(self):
        colours = shape.colour_set({"light": "#FFFFFF", "mid": "#888888", "deep": "#000000",
                                    "plate": "#0A1C26", "text": "#F2FBFC", "accent": "#F0B45C"})
        alpha = shape.minimum_opacity(colours, "plain")
        plates = shape.plate_vertices(colours["plate"], "plain")
        self.assertTrue(shape.proven(shape._foregrounds(colours), alpha, plates))
        self.assertFalse(shape.proven(shape._foregrounds(colours), alpha - 1 / 255, plates))
        # Checked against the extreme worlds directly, text over the plate.
        for world in ((0, 0, 0), (1, 1, 1)):
            plate = shape._rgb(colours["plate"])
            composite = tuple(alpha * p + (1 - alpha) * w for p, w in zip(plate, world))
            self.assertGreaterEqual(shape.contrast_ratio(colours["text"], composite), shape.TEXT_RATIO)
        self.assertGreaterEqual(shape.minimum_opacity(colours, "glass"), shape.GLASS_MIN_OPACITY)

    def test_a_failing_set_falls_back_as_a_whole(self):
        notes = []
        palette = {"light": "#FFFFFF", "mid": "#888888", "deep": "#000000",
                   "plate": "#0A1C26", "text": "#F2FBFC", "accent": "#0A1C26"}   # focus invisible on its plate
        surfaces = shape.surfaces_for(palette, {"entries": "glass", "panel": "solid"}, notes)
        self.assertEqual([n.code for n in notes], ["shape_contrast"])
        colours = surfaces["colours"]
        self.assertEqual(colours["source"], "neutral")
        self.assertEqual({k: colours[k] for k in shape.NEUTRAL}, shape.NEUTRAL)   # text and plate together, never one

    def test_transition_plans_are_proven_for_every_frame(self):
        """Whatever the plan, the colours used hold at every point of the
        blend: sampled here densely as a check of the proof, not as the proof."""
        rng = random.Random(9)
        neutral_alpha = shape.neutral_opacity()
        seen = set()
        for _ in range(60):
            palette = {k: "#%02X%02X%02X" % tuple(rng.randrange(256) for _ in range(3))
                       for k in ("light", "mid", "deep", "plate", "text", "accent")}
            colours = shape.colour_set(palette)
            material = rng.choice(shape.MATERIALS)
            alpha = shape.minimum_opacity(colours, material)
            if alpha is None or not shape._bar_holds(colours):
                continue
            plan = shape.transition_plan(shape.NEUTRAL, neutral_alpha, colours, material, alpha)
            seen.add(plan["plan"])
            if plan["plan"] == "cut":
                continue
            held = max(neutral_alpha, alpha)
            if plan["plan"] == "bridge":
                halves = [(shape.NEUTRAL, shape.NEUTRAL["plate"], "plain", plan["bridge"], "plain", 1.0),
                          (colours, plan["bridge"], "plain", colours["plate"], material, 1.0)]
            else:
                text = shape.NEUTRAL if plan["plan"] == "neutral-text" else colours
                halves = [(text, shape.NEUTRAL["plate"], "plain", colours["plate"], material, held)]
            for text, start, start_material, end, end_material, opacity in halves:
                a = shape._rgb(start)
                b = shape._rgb(end)
                for step in range(0, 65):
                    t = step / 64
                    plate = tuple(x + (y - x) * t for x, y in zip(a, b))
                    for world in ((0, 0, 0), (1, 1, 1), (1, 0, 0), (0, 1, 1)):
                        composite = tuple(opacity * p + (1 - opacity) * w for p, w in zip(plate, world))
                        self.assertGreaterEqual(shape.contrast_ratio(text["text"], composite), shape.TEXT_RATIO)
                        self.assertGreaterEqual(shape.contrast_ratio(text["focus"], composite), shape.FOCUS_RATIO)
        self.assertTrue(seen)

    def test_each_plan_occurs(self):
        def plan(palette, material):
            colours = shape.colour_set(palette)
            return shape.transition_plan(shape.NEUTRAL, shape.neutral_opacity(), colours, material,
                                         shape.minimum_opacity(colours, material))["plan"]
        base = shapetools.BASE_FIXTURE["palette"]
        self.assertEqual(plan(base, "solid"), "neutral-text")
        self.assertEqual(plan(base, "glass"), "shape-text")
        sea = json.loads((SAMPLES / "sea" / "shape.json").read_text())["palette"]
        self.assertEqual(plan(sea, "glass"), "bridge")
        paper = json.loads((SAMPLES / "paper" / "shape.json").read_text())["palette"]
        self.assertEqual(plan(paper, "paper"), "cut")

    def test_opposite_polarity_cannot_blend_and_cuts(self):
        paper = shape.colour_set({"light": "#FFFFFF", "mid": "#888888", "deep": "#000000",
                                  "plate": "#F3ECDF", "text": "#1F1C19", "accent": "#B8392A"})
        plan = shape.transition_plan(shape.NEUTRAL, shape.neutral_opacity(), paper, "paper", 1.0)
        self.assertEqual(plan["plan"], "cut")

    def test_lent_accent_is_kept_only_when_it_holds(self):
        self.assertEqual(shape.lent_focus("#2E7EC5")[0], "#2E7EC5")
        self.assertGreater(shape.lent_focus("#2E7EC5")[1], shape.neutral_opacity())
        self.assertEqual(shape.lent_focus("#202020"), (shape.NEUTRAL["focus"], shape.neutral_opacity()))
        self.assertEqual(shape.lent_focus(None)[0], shape.NEUTRAL["focus"])
        self.assertEqual(shape.lent_focus("red")[0], shape.NEUTRAL["focus"])


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def check(self, folder):
        return shape.check_package(DirectorySource(folder))

    def test_every_fixture_names_its_cause_and_drops_only_its_block(self):
        base = shapetools.write_fixture(self.tmp, "unknown-field")
        self.assertEqual(self.check(base).state, "ready")
        for name, (description, code, block, _) in shapetools.FIXTURES.items():
            with self.subTest(name=name):
                folder = shapetools.write_fixture(self.tmp, name) if name != "unknown-field" else base
                result = self.check(folder)
                self.assertIn(code, result.codes())
                if name in shapetools.FIXTURE_READY:
                    self.assertEqual(result.state, "ready")
                    self.assertEqual(shape.exit_status(result), 0)
                    continue
                self.assertEqual(shape.exit_status(result), 2)
                if block is None:
                    self.assertEqual(result.state, "unused")
                    self.assertIsNone(result.shape)
                    continue
                self.assertEqual(result.state, "partial")
                note = next(n for n in result.notes if n.code == code)
                self.assertEqual(note.block, block)
                if code == "shape_contrast":
                    self.assertIn("palette", result.shape)          # the world keeps its tones
                    self.assertEqual(result.shape["surfaces"]["colours"]["source"], "neutral")
                else:
                    self.assertNotIn(block, result.shape)
                    kept = [b for b in ("palette", "card", "world", "transition", "sounds") if b != block]
                    self.assertTrue(all(b in result.shape for b in kept), (kept, list(result.shape)))

    def test_budget_admits_blocks_in_order_and_counts_a_shared_file_once(self):
        folder = shapetools.write_fixture(self.tmp, "unknown-field")
        sizes = {path: (folder / path).stat().st_size for path in ("sfx/move.wav", "sfx/enter.wav", "sfx/back.wav")}
        with unittest.mock.patch.object(shape, "PACKAGE_MAX_BYTES", sum(sizes.values()) + 10):
            result = self.check(folder)
        self.assertIn("sounds", result.shape)
        self.assertNotIn("world", result.shape)
        self.assertEqual([n.block for n in result.notes if n.code == "shape_budget"], ["world"])
        document = json.loads((folder / "shape.json").read_text())
        document["world"]["layers"][0]["image"] = "world/backdrop.png"   # the backdrop twice
        document["world"]["light"] = []
        (folder / "shape.json").write_text(json.dumps(document))
        files = ["world/backdrop.png", "world/sprite.png"]
        world_bytes = sum((folder / f).stat().st_size for f in files)
        with unittest.mock.patch.object(shape, "PACKAGE_MAX_BYTES", sum(sizes.values()) + world_bytes):
            self.assertIn("world", self.check(folder).shape)

    def test_no_package_on_a_card_and_a_package_that_is_not_a_folder(self):
        staging = self.tmp / "card"
        image.populate(staging)
        info = validate_card(DirectorySource(staging))
        self.assertEqual(shape.check_card(DirectorySource(staging), info.root).state, "none")
        (staging / "content" / "mun-shape").write_text("not a folder")
        result = shape.check_card(DirectorySource(staging), info.root)
        self.assertEqual((result.state, result.codes()), ("unused", ["shape_unreadable"]))
        (staging / "content" / "mun-shape").unlink()
        os.symlink(SAMPLES / "sea", staging / "content" / "mun-shape")
        result = shape.check_card(DirectorySource(staging), info.root)
        self.assertEqual((result.state, result.codes()), ("unused", ["shape_unreadable"]))

    def test_a_card_stays_the_same_valid_card_with_any_package(self):
        for sample in ("sea", "not-json", "png-bomb"):
            staging = self.tmp / f"card-{sample}"
            image.populate(staging)
            before = validate_card(DirectorySource(staging)).to_dict()
            source = SAMPLES / sample if sample == "sea" else shapetools.write_fixture(self.tmp / "fx", sample)
            shutil.copytree(source, staging / "content" / "mun-shape", symlinks=True)
            self.assertEqual(validate_card(DirectorySource(staging)).to_dict(), before)
            result = shape.check_card(DirectorySource(staging), before["root"])
            self.assertEqual(result.state, "ready" if sample == "sea" else ("unused" if sample == "not-json" else "partial"))

    @unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
    def test_card_image_reads_as_the_folder_does(self):
        staging = self.tmp / "card"
        image.populate(staging)
        shutil.copytree(SAMPLES / "paper", staging / "content" / "mun-shape")
        card = self.tmp / "shape.img"
        image.create_image(card, staging)
        source = DebugfsSource(card, image.find_tool("debugfs"))
        info = validate_card(source)
        from_image = shape.check_card(source, info.root)
        from_folder = shape.check_package(DirectorySource(SAMPLES / "paper"))
        self.assertEqual(from_image.to_dict(), from_folder.to_dict())
        self.assertEqual(from_image.state, "ready")


class SampleTests(unittest.TestCase):
    """The two sample packages: one contract, two identities."""

    def result(self, name):
        return shape.check_package(DirectorySource(SAMPLES / name))

    def test_both_samples_are_used_entirely(self):
        for name in ("sea", "paper"):
            result = self.result(name)
            self.assertEqual((result.state, result.notes), ("ready", []), name)
            folder = SAMPLES / name
            named = set(result.files) | {"shape.json"}
            present = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
            self.assertEqual(present, named, name)       # nothing unused, nothing missing
            for display, values in shape.memory_estimate(result).items():
                self.assertLessEqual(values["total"], values["objective"], (name, display))

    def test_the_identities_differ_in_every_aspect_but_the_contract(self):
        sea, paper = self.result("sea").shape, self.result("paper").shape
        self.assertEqual(sea["format"], paper["format"])
        self.assertNotEqual(sea["palette"], paper["palette"])
        self.assertGreater(shape.luminance(paper["palette"]["plate"]), 0.5)   # light plates, dark text
        self.assertLess(shape.luminance(sea["palette"]["plate"]), 0.05)       # dark plates, light text
        self.assertNotEqual(sea["card"]["shape"], paper["card"]["shape"])
        self.assertNotEqual(shape.world_class(sea), shape.world_class(paper))
        self.assertTrue(sea["world"]["light"])
        self.assertFalse(paper["world"]["light"])
        self.assertEqual({s["material"] for s in (sea["surfaces"]["entries"], sea["surfaces"]["panel"])}, {"glass"})
        self.assertEqual({s["material"] for s in (paper["surfaces"]["entries"], paper["surfaces"]["panel"])}, {"paper"})
        self.assertNotEqual(sea["transition"]["in"], paper["transition"]["in"])
        self.assertNotEqual(sea["surfaces"]["entries"]["plan"], paper["surfaces"]["entries"]["plan"])
        self.assertFalse(set(sea["files"]) & set(paper["files"]) - {"card/window.png", "world/backdrop.png",
                                                                       "sfx/move.wav", "sfx/enter.wav",
                                                                       "sfx/back.wav", "sfx/insert.wav"})
        for common in ("card/window.png", "world/backdrop.png", "sfx/insert.wav"):
            self.assertNotEqual((SAMPLES / "sea" / common).read_bytes(), (SAMPLES / "paper" / common).read_bytes())

    def test_no_sample_identifier_reaches_the_console_services(self):
        identifiers = set()
        for name in ("sea", "paper"):
            identifiers.add(f"examples/shape/{name}")
            for path in self.result(name).files:
                identifiers.add(Path(path).name)
        # The contract's own file names in the template are not a sample's.
        identifiers -= {"backdrop.png", "window.png", "move.wav", "enter.wav", "back.wav", "insert.wav"}
        pattern = re.compile("|".join(re.escape(i) for i in sorted(identifiers)))
        for path in (ROOT / "services").rglob("*"):
            if path.is_file() and path.suffix in (".qml", ".cpp", ".h", ".py", ".js", ".txt", ".service"):
                found = pattern.search(path.read_text(encoding="utf-8", errors="replace"))
                self.assertIsNone(found, f"{path.relative_to(ROOT)}: {found and found.group(0)}")

    def test_the_generator_remakes_the_small_files(self):
        spec = importlib.util.spec_from_file_location("shape_generate", SAMPLES / "generate.py")
        generate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generate)
        for name in ("sea", "paper"):
            for relative, recipe in generate.PACKAGES[name]().items():
                path = SAMPLES / name / relative
                if relative == "shape.json":
                    self.assertEqual(path.read_bytes(), shapetools.dump(recipe).encode("utf-8"))
                elif relative.endswith(".wav") and "insert" not in relative:
                    # Within one step per sample: another libm may round a
                    # last digit differently.
                    self.assertClose(path.read_bytes(), recipe(), relative, header=44, width=2)
                elif relative.endswith(".png") and path.stat().st_size < 4096:
                    make, alpha = recipe
                    rows = make()
                    remade = shapetools.encode_png(len(rows[0]) // (4 if alpha else 3), len(rows), rows, alpha)
                    self.assertClose(b"".join(shapetools.decode_png(path.read_bytes())[2]),
                                     b"".join(shapetools.decode_png(remade)[2]), relative)

    def assertClose(self, a, b, label, header=0, width=1):
        self.assertEqual((len(a), a[:header]), (len(b), b[:header]), label)
        if width == 2:
            left = struct.unpack(f"<{(len(a) - header) // 2}h", a[header:])
            right = struct.unpack(f"<{(len(b) - header) // 2}h", b[header:])
        else:
            left, right = a[header:], b[header:]
        self.assertLessEqual(max((abs(x - y) for x, y in zip(left, right)), default=0), 1, label)


class ReadLevelTests(unittest.TestCase):
    def test_palette_is_reproducible_from_fixed_covers(self):
        tmp = Path(tempfile.mkdtemp(prefix="cover-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        image.write_cover(tmp / "cover.png", 512, 512)
        self.assertEqual(shapetools.read_palette((tmp / "cover.png").read_bytes()),
                         {"light": "#F4F1E9", "hi": "#F4F1E9", "mid": "#F4F1E9", "low": "#E7D2BD",
                          "deep": "#B6613A", "accent": "#EB7D4A"})
        self.assertEqual(shapetools.read_palette((SAMPLES / "sea" / "card" / "window.png").read_bytes()),
                         {"light": "#9EF0F7", "hi": "#5BBED2", "mid": "#1C6B84", "low": "#082A3C",
                          "deep": "#031E2C", "accent": None})

    def test_every_encoding_of_the_same_pixels_reads_the_same(self):
        rng = random.Random(3)
        width, height = 37, 23
        colours = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(6)]
        indices = [[rng.randrange(6) for _ in range(width)] for _ in range(height)]
        rgb = [[c for i in row for c in colours[i]] for row in indices]
        rgba = [[c for i in row for c in colours[i] + (255,)] for row in indices]
        rgb16 = [[c * 257 for c in row] for row in rgb]
        palette = b"".join(bytes(c) for c in colours)
        expected = shapetools.read_palette(png(width, height, rgb, colour=2))
        for label, data in {
            "rgba": png(width, height, rgba, colour=6),
            "rgb16": png(width, height, rgb16, colour=2, depth=16),
            "indexed": png(width, height, indices, colour=3, palette=palette),
            "interlaced": png(width, height, rgb, colour=2, interlace=1),
            "indexed-4bit-interlaced": png(width, height, indices, colour=3, depth=4, interlace=1, palette=palette),
        }.items():
            self.assertEqual(shapetools.read_palette(data), expected, label)

    def test_greys_bits_and_transparency_decode(self):
        grey = [[0, 1, 2, 3] * 2] * 3
        _, _, rows = shapetools.decode_png(png(8, 3, grey, colour=0, depth=2))
        self.assertEqual(rows[0][:12], bytes([0, 0, 0, 85, 85, 85, 170, 170, 170, 255, 255, 255]))
        # Transparent pixels composite over black.
        _, _, rows = shapetools.decode_png(png(2, 1, [[255, 255, 255, 0, 255, 0, 0, 128]], colour=6))
        self.assertEqual(rows[0], bytes([0, 0, 0, 128, 0, 0]))
        _, _, rows = shapetools.decode_png(png(2, 1, [[0, 1]], colour=3, palette=b"\xff\0\0\0\xff\0", transparency=b"\0"))
        self.assertEqual(rows[0], bytes([0, 0, 0, 0, 255, 0]))

    def test_decoder_refuses_what_it_cannot_bound(self):
        with self.assertRaises(Exception):
            shapetools.decode_png(shapetools._bomb_png())
        with self.assertRaises(Exception):
            shapetools.decode_png(shapetools.encode_png(4, 4, [bytes(12)] * 4, alpha=False)[:-20])


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_template_is_ready_and_reads_its_cover(self):
        image.write_cover(self.tmp / "cover.png", 256, 256)
        code, out, _ = self.run_cli("shape", "init", str(self.tmp / "pkg"), "--cover", str(self.tmp / "cover.png"))
        self.assertEqual(code, 0)
        document = json.loads((self.tmp / "pkg" / "shape.json").read_text())
        read = shapetools.read_palette((self.tmp / "cover.png").read_bytes())
        self.assertEqual((document["palette"]["deep"], document["palette"]["accent"]), (read["deep"], read["accent"]))
        self.assertEqual(self.run_cli("shape", "check", str(self.tmp / "pkg"))[0], 0)
        code, _, err = self.run_cli("shape", "init", str(self.tmp / "pkg"))
        self.assertEqual(code, 1)
        self.assertIn("shape_exists", err)
        self.assertEqual(self.run_cli("shape", "init", str(self.tmp / "pkg"), "--force")[0], 0)

    def test_example_copy_and_json_report(self):
        self.assertEqual(self.run_cli("shape", "init", str(self.tmp / "sea"), "--example", "sea")[0], 0)
        code, out, _ = self.run_cli("shape", "check", str(self.tmp / "sea"), "--json")
        report = json.loads(out)
        self.assertEqual((code, report["state"], report["undeclared_files"]), (0, "ready", []))
        self.assertEqual(report["shape"], shape.check_package(DirectorySource(SAMPLES / "sea")).shape)
        self.assertEqual(report["world_class"], "20 fps")

    def test_exit_status_and_fixtures_on_disk(self):
        code, out, _ = self.run_cli("shape", "variants")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), len(shapetools.FIXTURES))
        self.assertEqual(self.run_cli("shape", "variants", str(self.tmp / "fx"))[0], 0)
        code, out, _ = self.run_cli("shape", "check", str(self.tmp / "fx" / "png-bomb"), "--report")
        self.assertEqual(code, 2)
        self.assertIn("shape_png_dimensions", out)
        self.assertEqual(self.run_cli("shape", "check", str(self.tmp / "missing"))[0], 1)


def snapshot(*roots):
    """Every entry under the roots: kind, and bytes or link target."""
    state = {}
    for root in roots:
        for path in sorted([root, *root.rglob("*")]) if root.exists() or root.is_symlink() else []:
            key = str(path)
            if path.is_symlink():
                state[key] = ("link", os.readlink(path))
            elif path.is_dir():
                state[key] = ("dir",)
            elif path.is_file():
                state[key] = ("file", path.read_bytes(), path.stat().st_ino)
    return state


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


class InitDestinationTests(unittest.TestCase):
    """init writes nothing unless the whole destination is safe, never
    follows a link, and --force only replaces its own names' regular files."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-init-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "window.png").write_bytes(b"OUTSIDE ART")
        (self.outside / "keep.json").write_bytes(b"OUTSIDE JSON")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def refused(self, folder, code, *extra):
        before = snapshot(self.tmp)
        for force in ((), ("--force",)) if code != "shape_exists" else ((),):
            result = self.run_cli("shape", "init", str(folder), *extra, *force)
            self.assertEqual(result[0], 1, result)
            self.assertIn(f"[{code}]", result[2])
            self.assertEqual(snapshot(self.tmp), before, "nothing may be written or changed")

    def test_existing_artwork_is_kept(self):
        folder = self.tmp / "pkg"
        (folder / "card").mkdir(parents=True)
        (folder / "card" / "window.png").write_bytes(b"OWN ART")
        (folder / "notes.txt").write_bytes(b"mine")
        self.refused(folder, "shape_exists", "--example", "sea")
        # --force replaces the sample's own names and nothing else.
        self.assertEqual(self.run_cli("shape", "init", str(folder), "--example", "sea", "--force")[0], 0)
        self.assertEqual((folder / "card" / "window.png").read_bytes(),
                         (SAMPLES / "sea" / "card" / "window.png").read_bytes())
        self.assertEqual((folder / "notes.txt").read_bytes(), b"mine")
        self.assertFalse([p for p in folder.rglob(".*")], "no temporary file is left")

    def test_a_linked_folder_on_the_way_is_never_followed(self):
        folder = self.tmp / "pkg"
        folder.mkdir()
        (folder / "card").symlink_to(self.outside, target_is_directory=True)
        self.refused(folder, "shape_destination_link", "--example", "sea")

    def test_a_dangling_link_at_a_final_name_is_refused(self):
        folder = self.tmp / "pkg"
        folder.mkdir()
        (folder / "shape.json").symlink_to(self.tmp / "created-outside.json")
        self.refused(folder, "shape_destination_link")
        self.assertFalse((self.tmp / "created-outside.json").exists())
        (folder / "shape.json").unlink()
        (folder / "README.md").symlink_to(self.outside / "keep.json")
        self.refused(folder, "shape_destination_link")

    def test_the_folder_itself_must_not_be_a_link_or_a_file(self):
        link = self.tmp / "pkg"
        link.symlink_to(self.outside, target_is_directory=True)
        self.refused(link, "shape_destination_link")
        file = self.tmp / "file"
        file.write_bytes(b"x")
        self.refused(file, "shape_destination_type")

    def test_a_folder_where_a_file_goes_is_refused(self):
        folder = self.tmp / "pkg"
        (folder / "shape.json").mkdir(parents=True)
        self.refused(folder, "shape_destination_type")
        shutil.rmtree(folder)
        (folder / "world").mkdir(parents=True)
        (folder / "card").write_bytes(b"a file where a folder goes")
        self.refused(folder, "shape_destination_type", "--example", "sea")

    def test_force_never_truncates_a_file_linked_elsewhere(self):
        folder = self.tmp / "pkg"
        folder.mkdir()
        os.link(self.outside / "keep.json", folder / "shape.json")
        self.refused(folder, "shape_exists")
        self.assertEqual(self.run_cli("shape", "init", str(folder), "--force")[0], 0)
        self.assertEqual((self.outside / "keep.json").read_bytes(), b"OUTSIDE JSON")
        self.assertEqual(json.loads((folder / "shape.json").read_text())["format"], shape.FORMAT)

    def test_a_new_folder_is_created_whole(self):
        folder = self.tmp / "a" / "b" / "pkg"
        self.assertEqual(self.run_cli("shape", "init", str(folder), "--example", "paper")[0], 0)
        written = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
        self.assertEqual(written, {p.relative_to(SAMPLES / "paper").as_posix()
                                   for p in (SAMPLES / "paper").rglob("*") if p.is_file()})


class LateDestinationTests(unittest.TestCase):
    """A name absent when init checks the destination, created just before
    init publishes its file there (the race is injected at the publishing
    call, with real files): without --force it is kept, bytes and inode,
    the temporary is removed and init stops with a controlled error."""

    SENTINEL = b"CONCURRENT EDIT: DO NOT REPLACE\n"
    # The real calls, for the concurrent writer while init's are patched.
    REAL_LINK = staticmethod(os.link)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-late-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_with_arrival(self, folder, target, arrival, *extra):
        """Run init; just before the file named like `target` is published,
        `arrival(name, dir_fd)` creates something there."""
        real_link, real_rename = os.link, os.rename
        seen = {}

        def arrive(destination, kwargs):
            if destination == Path(target).name and not seen:
                seen["inode"] = arrival(destination, kwargs["dst_dir_fd"])

        def link(source, destination, **kwargs):
            arrive(destination, kwargs)
            return real_link(source, destination, **kwargs)

        def rename(source, destination, **kwargs):
            arrive(destination, kwargs)
            return real_rename(source, destination, **kwargs)
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(shapetools.os, "link", side_effect=link), \
                unittest.mock.patch.object(shapetools.os, "rename", side_effect=rename), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["shape", "init", str(folder), *extra])
        self.assertIn("inode", seen, "the arrival was injected")
        return code, err.getvalue(), seen["inode"]

    def concurrent_file(self, name, directory_fd):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=directory_fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(self.SENTINEL)
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_ino

    def assert_kept(self, folder, target, code, err, inode):
        self.assertEqual(code, 1)
        self.assertIn("[shape_exists]", err)
        self.assertIn(target, err)
        path = folder / target
        self.assertEqual(path.read_bytes(), self.SENTINEL)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual([p.name for p in folder.rglob(".*")], [], "no temporary is left")

    def test_a_manifest_that_appears_late_is_kept(self):
        folder = self.tmp / "template"
        code, err, inode = self.run_with_arrival(folder, "shape.json", self.concurrent_file)
        self.assert_kept(folder, "shape.json", code, err, inode)

    def test_a_resource_that_appears_late_is_kept(self):
        folder = self.tmp / "sample"
        code, err, inode = self.run_with_arrival(folder, "card/window.png", self.concurrent_file, "--example", "sea")
        self.assert_kept(folder, "card/window.png", code, err, inode)
        # It is the first file published: init stops there and writes nothing more.
        self.assertEqual([p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()],
                         ["card/window.png"])

    def test_the_files_published_before_are_named(self):
        folder = self.tmp / "sample"
        code, err, inode = self.run_with_arrival(folder, "world/fish.png", self.concurrent_file, "--example", "sea")
        self.assert_kept(folder, "world/fish.png", code, err, inode)
        self.assertIn("ya escritos: card/window.png", err)

    def test_a_link_that_appears_late_is_kept_and_not_followed(self):
        folder = self.tmp / "template"
        outside = self.tmp / "created-outside.json"

        def concurrent_link(name, directory_fd):
            os.symlink(str(outside), name, dir_fd=directory_fd)
            return os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_ino
        code, err, inode = self.run_with_arrival(folder, "shape.json", concurrent_link)
        self.assertEqual(code, 1)
        self.assertIn("[shape_exists]", err)
        self.assertTrue((folder / "shape.json").is_symlink())
        self.assertEqual(os.lstat(folder / "shape.json").st_ino, inode)
        self.assertFalse(outside.exists())
        self.assertEqual([p.name for p in folder.rglob(".*")], [])

    def test_with_force_a_late_file_is_replaced_only_at_its_name(self):
        folder = self.tmp / "template"
        other = self.tmp / "elsewhere.json"

        def concurrent_hard_link(name, directory_fd):
            other.write_bytes(self.SENTINEL)
            self.REAL_LINK(str(other), name, dst_dir_fd=directory_fd)
            return other.stat().st_ino
        code, err, inode = self.run_with_arrival(folder, "shape.json", concurrent_hard_link, "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(other.read_bytes(), self.SENTINEL)          # replaced by name, never truncated
        self.assertEqual(json.loads((folder / "shape.json").read_text())["format"], shape.FORMAT)
        self.assertEqual([p.name for p in folder.rglob(".*")], [])

    def test_a_volume_without_hard_links_is_a_controlled_error(self):
        folder = self.tmp / "template"
        import errno
        with unittest.mock.patch.object(shapetools.os, "link", side_effect=OSError(errno.ENOTSUP, "not supported")):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["shape", "init", str(folder)])
        self.assertEqual(code, 1)
        self.assertIn("[shape_destination_unsupported]", err.getvalue())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [], "nothing is published, no temporary left")

    def test_an_ordinary_init_leaves_no_temporary(self):
        folder = self.tmp / "plain"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["shape", "init", str(folder)]), 0)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["README.md", "shape.json"])
        self.assertEqual((folder / "shape.json").stat().st_nlink, 1)


class DamagedCoverTests(unittest.TestCase):
    """A damaged cover is an error of the file, with a stable code, for
    both commands that read one; nothing is written."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-cover-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        good = shapetools.encode_png(1, 1, [bytes([100, 120, 140])], False)
        ihdr = good[8:33]
        grey = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        stream = zlib.compress(bytes([0, 100, 120, 140]))
        self.covers = {
            # The two files of the review: a zlib header over an invalid deflate
            # block, and a grey image whose tRNS has one byte instead of two.
            "bad-deflate": good[:8] + ihdr + chunk(b"IDAT", b"\x78\x9c\x07") + chunk(b"IEND", b""),
            "short-trns": good[:8] + grey + chunk(b"tRNS", b"\x00") + chunk(b"IDAT", zlib.compress(bytes([0, 50])))
            + chunk(b"IEND", b""),
            "truncated-stream": good[:8] + ihdr + chunk(b"IDAT", stream[:-5]) + chunk(b"IEND", b""),
            "data-after-stream": good[:8] + ihdr + chunk(b"IDAT", stream + b"extra") + chunk(b"IEND", b""),
            "too-much-data": good[:8] + ihdr + chunk(b"IDAT", zlib.compress(bytes(40))) + chunk(b"IEND", b""),
            "trns-on-rgba": png(1, 1, [[1, 2, 3, 4]], colour=6)[:33] + chunk(b"tRNS", b"\x00" * 6)
            + png(1, 1, [[1, 2, 3, 4]], colour=6)[33:],
            "not-png": b"GIF89a",
        }

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_both_commands_report_the_cover_without_a_traceback(self):
        for name, data in self.covers.items():
            with self.subTest(name=name):
                cover = self.tmp / f"{name}.png"
                cover.write_bytes(data)
                code, _, err = self.run_cli("shape", "check", str(SAMPLES / "sea"), "--cover", str(cover))
                self.assertEqual(code, 1)
                self.assertIn("[cover_invalid]", err)
                folder = self.tmp / f"init-{name}"
                code, _, err = self.run_cli("shape", "init", str(folder), "--cover", str(cover))
                self.assertEqual(code, 1)
                self.assertIn("[cover_invalid]", err)
                self.assertFalse(folder.exists(), "a refused cover writes nothing")

    def test_decoding_is_bounded(self):
        big = self.tmp / "big.png"
        big.write_bytes(png(1, 1, [[0, 0, 0]], colour=2)[:33] + chunk(b"zTXt", b"k\0\0" + bytes(1024 * 1024))
                        + png(1, 1, [[0, 0, 0]], colour=2)[33:])
        code, _, err = self.run_cli("shape", "check", str(SAMPLES / "sea"), "--cover", str(big))
        self.assertEqual((code, "[cover_too_large]" in err), (1, True))
        wide = self.tmp / "wide.png"
        wide.write_bytes(png(1025, 1, [[0, 0, 0] * 1025], colour=2))
        code, _, err = self.run_cli("shape", "check", str(SAMPLES / "sea"), "--cover", str(wide))
        self.assertEqual((code, "[cover_too_large]" in err), (1, True))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-report-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_silent_sound_is_valid_and_the_report_is_strict_json(self):
        folder = self.tmp / "silent"
        folder.mkdir()
        (folder / "zero.wav").write_bytes(shapetools.encode_wav([(0, 0)] * 480))
        (folder / "shape.json").write_text(json.dumps({"format": "mun-shape/1",
                                                       "sounds": {k: "zero.wav" for k in ("move", "enter", "back")}}))
        code, out, _ = self.run_cli("shape", "check", str(folder), "--json")
        self.assertEqual(code, 0)

        def refuse(constant):
            raise ValueError(constant)
        report = json.loads(out, parse_constant=refuse)
        self.assertIsNone(report["files"]["zero.wav"]["peak_dbfs"])
        self.assertEqual(report["state"], "ready")
        code, out, _ = self.run_cli("shape", "check", str(folder))
        self.assertEqual(code, 0)
        self.assertIn("silencio", out)
        self.assertEqual(shape.wav_header(shapetools.encode_wav([(0, 1)] * 4))["peak_dbfs"],
                         20 * __import__("math").log10(1 / 32768))

    def test_the_cause_is_shown_when_no_block_survives(self):
        folder = self.tmp / "single"
        folder.mkdir()
        (folder / "shape.json").write_text(json.dumps({"format": "mun-shape/1", "card": {"window": "missing.png"}}))
        for extra in ((), ("--report",)):
            code, out, _ = self.run_cli("shape", "check", str(folder), *extra)
            self.assertEqual(code, 2)
            for expected in ("SIN USO", "shape_path_missing", "card.window", "missing.png"):
                self.assertIn(expected, out, (extra, out))
        (folder / "shape.json").write_text(json.dumps({"format": "mun-shape/1", "card": {"morph": 2},
                                                       "transition": {"in": "spin"}}))
        code, out, _ = self.run_cli("shape", "check", str(folder))
        for expected in ("shape_range", "card.morph", "shape_enum", "transition.in"):
            self.assertIn(expected, out)


class DocumentationTests(unittest.TestCase):
    def test_the_contract_names_every_code_and_limit(self):
        text = (ROOT / "docs" / "shape.md").read_text(encoding="utf-8")
        for code in shape.NOTE_CODES:
            self.assertIn(f"`{code}`", text, code)
        for fixture in shapetools.FIXTURES:
            self.assertIn(f"`{fixture}`", text, fixture)
        for phrase in ("64 KiB", "32 MiB", "4 MiB", "1 MiB", "2048", "256", "1024", "128", "−1 dBFS", "4.5:1", "3:1",
                       "136 MiB", "200 MiB"):
            self.assertIn(phrase, text, phrase)
        for code in ("shape_exists", "shape_destination_link", "shape_destination_type",
                     "shape_destination_unsupported", "cover_invalid",
                     "cover_too_large", "`null`"):
            self.assertIn(code, text, code)
        self.assertIn("mun-shape", (ROOT / "docs" / "game-cards.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
