"""Host-side tests for the Game Card v0 format: parser, validator, images."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import ext4, image, minitoml  # noqa: E402
from mun_card.errors import CardError, ERROR_CODES  # noqa: E402
from mun_card.source import DebugfsSource, DirectorySource  # noqa: E402
from mun_card.validate import MANIFEST_NAMES, validate_card  # noqa: E402

# The manifest a new card carries (docs/game-cards.md); the earlier name is tested apart.
MANIFEST = MANIFEST_NAMES["mun"]


def have_e2fsprogs() -> bool:
    try:
        image.find_tool("mke2fs")
        image.find_tool("debugfs")
        return True
    except CardError:
        return False


class MiniTomlTests(unittest.TestCase):
    def test_subset_round_trip(self):
        data = minitoml.loads('# c\n[card]\nschema = 1\nid = "a.b"  # x\nflag = true\n[content]\nlist = ["a", "b"]\nlit = \'raw "q"\'\n')
        self.assertEqual(data["card"]["schema"], 1)
        self.assertEqual(data["card"]["id"], "a.b")
        self.assertTrue(data["card"]["flag"])
        self.assertEqual(data["content"]["list"], ["a", "b"])
        self.assertEqual(data["content"]["lit"], 'raw "q"')

    def test_rejects_unsupported_syntax(self):
        for text in ("[[arr]]\n", "key = 1.5\n", "key = {a = 1}\n", 'key = "open\n', "key\n", "a = 1\na = 2\n",
                     'k = "\\x41"\n', "k = [1, 2]\n"):
            with self.assertRaises(minitoml.TomlSyntaxError, msg=text):
                minitoml.loads(text)

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
    def test_agrees_with_tomllib_on_valid_manifest(self):
        import tomllib
        self.assertEqual(minitoml.loads(image.VALID_MANIFEST), tomllib.loads(image.VALID_MANIFEST))


class ValidatorTests(unittest.TestCase):
    """Validation against staged directories: no e2fsprogs needed."""

    def staged(self, variant: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="card-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        staging = tmp / "card"
        staging.mkdir()
        image.populate(staging, variant)
        return staging

    def test_valid_card(self):
        info = validate_card(DirectorySource(self.staged("valid")))
        self.assertEqual((info.id, info.title, info.version, info.kind), ("mun.testcard", "MUN Test Card", "0.1.0", "test"))
        self.assertEqual(info.cover, "cover.png")
        self.assertFalse(info.runnable)
        self.assertEqual(info.saves, "saves")

    def test_each_defect_maps_to_its_code(self):
        expected = {
            "missing-manifest": "manifest_missing", "ambiguous-manifest": "manifest_ambiguous",
            "bad-toml": "manifest_syntax",
            "missing-fields": "manifest_field", "bad-schema": "schema_unsupported",
            "bad-arch": "arch_unsupported", "bad-profile": "profile_unsupported",
            "unsafe-path": "path_unsafe", "absolute-path": "path_unsafe",
            "missing-file": "path_missing", "symlink": "path_symlink",
            "cover-too-big": "cover_too_large", "cover-not-png": "cover_invalid",
            "game-without-entry": "manifest_field",
        }
        self.assertEqual(set(expected) | {"valid", "game", "game-bad-entry", "game-gl", "cover-near-limit", "full"}, set(image.VARIANTS))
        for variant, code in expected.items():
            with self.assertRaises(CardError, msg=variant) as ctx:
                validate_card(DirectorySource(self.staged(variant)))
            self.assertEqual(ctx.exception.code, code, variant)
            self.assertIn(code, ERROR_CODES)

    def test_manifest_size_limit_and_symlinked_manifest(self):
        staging = self.staged("valid")
        (staging / MANIFEST).write_text("# " + "x" * (64 * 1024) + "\n")
        with self.assertRaises(CardError) as ctx:
            validate_card(DirectorySource(staging))
        self.assertEqual(ctx.exception.code, "manifest_too_large")
        (staging / MANIFEST).unlink()
        os.symlink("/etc/hostname", staging / MANIFEST)
        with self.assertRaises(CardError) as ctx:
            validate_card(DirectorySource(staging))
        self.assertEqual(ctx.exception.code, "path_symlink")

    def test_symlink_in_middle_of_path_is_rejected(self):
        staging = self.staged("valid")
        os.rename(staging / "content", staging / "real")
        os.symlink("real", staging / "content")
        with self.assertRaises(CardError) as ctx:
            validate_card(DirectorySource(staging))
        self.assertEqual(ctx.exception.code, "path_symlink")

    def test_dotdot_and_backslash_paths(self):
        for bad in ("content/../x", "./content", "a\\b", "content/", "content//x", 'con"tent', "con tent", "-x/y", "ñ"):
            staging = self.staged("valid")
            # Literal strings so a backslash reaches the validator instead of the TOML parser.
            text = (staging / MANIFEST).read_text().replace('root = "content"', f"root = '{bad}'")
            (staging / MANIFEST).write_text(text)
            with self.assertRaises(CardError, msg=bad) as ctx:
                validate_card(DirectorySource(staging))
            self.assertEqual(ctx.exception.code, "path_unsafe", bad)

    def test_cover_near_limit_is_valid_and_incompressible(self):
        from mun_card.validate import COVER_MAX_BYTES
        staging = self.staged("cover-near-limit")
        size = (staging / "cover.png").stat().st_size
        self.assertTrue(COVER_MAX_BYTES - 8192 < size <= COVER_MAX_BYTES, size)
        info = validate_card(DirectorySource(staging))
        self.assertEqual(info.cover, "cover.png")
        import base64
        self.assertGreater(len(base64.b64encode((staging / "cover.png").read_bytes())), 1 << 20,
                           "the wire frame for a legal cover exceeds 1 MiB: consumers must accept more")

    def test_game_gl_variant_names_the_entry_after_the_binary_and_carries_the_provisional_profile(self):
        tmp = Path(tempfile.mkdtemp(prefix="mun-gl-")); self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "mun-gl-probe"; fake.write_bytes(b"\x7fELF fake"); fake.chmod(0o755)
        staging = tmp / "gl"; staging.mkdir()
        image.populate(staging, "game-gl", game_binary=fake)
        info = validate_card(DirectorySource(staging))
        self.assertEqual((info.id, info.kind, info.entry, info.profile, info.title),
                         ("mun.munglprobe", "game", "content/mun-gl-probe", "linux-arm64-gl-v0", "mun-gl-probe"))
        with self.assertRaises(CardError):
            image.populate(tmp / "nogl", "game-gl")
        self.assertEqual(info.access, "copy", "no access field: the default")

    def test_a_card_can_have_its_own_identifier_and_version(self):
        # What the Game Card guide asks an author to choose; the console's own
        # rules are applied when the card is made, not only when it is read.
        tmp = Path(tempfile.mkdtemp(prefix="mun-id-")); self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "mun-collect"; fake.write_bytes(b"\x7fELF fake"); fake.chmod(0o755)
        staging = tmp / "mine"; staging.mkdir()
        image.populate(staging, "game", "My Collect", game_binary=fake, card_id="org.example.mycollect", version="1.2.0")
        info = validate_card(DirectorySource(staging))
        self.assertEqual((info.id, info.version, info.title), ("org.example.mycollect", "1.2.0", "My Collect"))
        for field, value, code in (("card_id", "Bad ID", "id_invalid"), ("version", "one", "version_invalid")):
            (tmp / field).mkdir()
            with self.assertRaises(CardError) as refused:
                image.populate(tmp / field, "game", game_binary=fake, **{field: value})
            self.assertEqual(refused.exception.code, code)

    def test_game_gl_content_directory_is_copied_and_declares_mount_access(self):
        tmp = Path(tempfile.mkdtemp(prefix="mun-glc-")); self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "game"; fake.write_bytes(b"\x7fELF fake"); fake.chmod(0o755)
        data = tmp / "data"; (data / "gfx" / "deep").mkdir(parents=True)
        (data / "gfx" / "deep" / "a.png").write_bytes(b"png"); (data / "readme.txt").write_text("hi")
        staging = tmp / "gl"; staging.mkdir()
        image.populate(staging, "game-gl", game_binary=fake, content_dir=data)
        info = validate_card(DirectorySource(staging))
        self.assertEqual(info.access, "mount")
        self.assertEqual((staging / "content" / "gfx" / "deep" / "a.png").read_bytes(), b"png")
        self.assertEqual((staging / "content" / "readme.txt").read_text(), "hi")
        (data / "link").symlink_to(data / "readme.txt")
        with self.assertRaises(CardError) as ctx:
            image.populate(tmp / "gl2", "game-gl", game_binary=fake, content_dir=data)
        self.assertEqual(ctx.exception.code, "content_symlink")
        bad = tmp / "bad"; bad.mkdir()
        image.populate(bad, "game-gl", game_binary=fake)
        text = (bad / MANIFEST).read_text().replace('kind = "game"', 'kind = "game"\naccess = "stream"')
        (bad / MANIFEST).write_text(text)
        with self.assertRaises(CardError) as ctx:
            validate_card(DirectorySource(bad))
        self.assertEqual(ctx.exception.code, "manifest_field")

    def test_presentation_colours_and_a_supplied_cover_travel_in_the_manifest(self):
        tmp = Path(tempfile.mkdtemp(prefix="mun-pres-")); self.addCleanup(shutil.rmtree, tmp, True)
        staging = tmp / "plain"; staging.mkdir()
        image.populate(staging, "valid")
        info = validate_card(DirectorySource(staging))
        self.assertEqual((info.accent, info.background), (None, None), "no table: the shell keeps its own identity")
        cover = tmp / "art.png"; image.write_cover(cover, 300, 400)
        themed = tmp / "themed"; themed.mkdir()
        image.populate(themed, "valid", cover=cover, accent="#2e7ec5", background="#C9E1F5")
        info = validate_card(DirectorySource(themed))
        self.assertEqual((info.accent, info.background), ("#2E7EC5", "#C9E1F5"))
        self.assertEqual((themed / "cover.png").read_bytes(), cover.read_bytes())
        self.assertIn("accent", info.to_dict())
        for bad in ('accent = "blue"', 'accent = "#12345"', 'accent = 7', 'background = "#GGGGGG"'):
            broken = tmp / ("bad" + str(abs(hash(bad)))); broken.mkdir()
            image.populate(broken, "valid")
            text = (broken / MANIFEST).read_text() + "\n[presentation]\n" + bad + "\n"
            (broken / MANIFEST).write_text(text)
            with self.assertRaises(CardError, msg=bad) as ctx:
                validate_card(DirectorySource(broken))
            self.assertEqual(ctx.exception.code, "manifest_field", bad)
        notpng = tmp / "cover.txt"; notpng.write_text("nope")
        with self.assertRaises(CardError) as ctx:
            image.populate(tmp / "np", "valid", cover=notpng)
        self.assertEqual(ctx.exception.code, "cover_invalid")

    def test_a_data_tree_that_claims_the_entry_path_is_refused_not_merged(self):
        # An installed game's directory often carries its own engine
        # under the name of the one being packaged.
        tmp = Path(tempfile.mkdtemp(prefix="mun-entry-")); self.addCleanup(shutil.rmtree, tmp, True)
        selected = tmp / "sample"; selected.write_bytes(b"ARM64-selected-engine"); selected.chmod(0o700)
        with_file = tmp / "data-file"; (with_file / "gfx").mkdir(parents=True)
        (with_file / "sample").write_bytes(b"OTHER-engine-from-data-tree")
        with_dir = tmp / "data-dir"; (with_dir / "sample" / "bin").mkdir(parents=True)
        for data in (with_file, with_dir):
            staging = tmp / ("staging-" + data.name); staging.mkdir()
            with self.assertRaises(CardError, msg=data.name) as ctx:
                image.populate(staging, "game-gl", game_binary=selected, content_dir=data)
            self.assertEqual(ctx.exception.code, "content_entry_conflict", data.name)
            self.assertIn("sample", ctx.exception.detail)
        clean = tmp / "data-clean"; (clean / "gfx").mkdir(parents=True); (clean / "gfx" / "a.png").write_bytes(b"png")
        (clean / "sample.png").write_bytes(b"icon")   # a similar name is not a collision
        staging = tmp / "staging-clean"; staging.mkdir()
        image.populate(staging, "game-gl", game_binary=selected, content_dir=clean)
        entry = staging / "content" / "sample"
        self.assertEqual(image.sha256_file(entry), image.sha256_file(selected), "the packaged entry is the selected file")
        self.assertEqual(entry.stat().st_mode & 0o777, 0o755)
        self.assertEqual((staging / "content" / "sample.png").read_bytes(), b"icon")
        self.assertEqual(validate_card(DirectorySource(staging)).entry, "content/sample")

    def test_game_variant_embeds_binary_and_bad_entry_validates_as_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="card-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "fake-game"
        fake.write_bytes(b"\x7fELF fake")
        staging = tmp / "game"; staging.mkdir()
        image.populate(staging, "game", game_binary=fake)
        info = validate_card(DirectorySource(staging))
        self.assertEqual((info.id, info.kind, info.entry, info.title), ("mun.collect", "game", "content/mun-collect", "MUN Collect"))
        bad = tmp / "bad"; bad.mkdir()
        image.populate(bad, "game-bad-entry")
        info = validate_card(DirectorySource(bad))   # validity is about the manifest; exec failure is the launcher's job
        self.assertEqual(info.entry, "content/not-a-binary")
        with self.assertRaises(CardError):
            image.populate(tmp / "nogame", "game")

    def test_game_kind_with_entry_is_described_not_runnable(self):
        staging = self.staged("valid")
        (staging / "content" / "game.bin").write_bytes(b"\x7fELF not really")
        text = (staging / MANIFEST).read_text().replace('kind = "test"', 'kind = "game"\nentry = "content/game.bin"')
        (staging / MANIFEST).write_text(text)
        info = validate_card(DirectorySource(staging))
        self.assertEqual(info.kind, "game")
        self.assertEqual(info.entry, "content/game.bin")
        self.assertTrue(info.runnable, "declares an entry; launching is the launcher's call")


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs not installed")
class DirectorySavesManifestTests(unittest.TestCase):
    """[saves] directory declarations (docs/saves.md): all or nothing, games only, bounded."""

    SAVES = {"directory": ".Sample/save", "units": ["*.sav", "screen-*.zsc"], "checks": ["zlib-xml", "zlib"],
             "max_bytes": 8 * 1024 * 1024}

    def staged(self, saves=None, variant="game-gl"):
        tmp = Path(tempfile.mkdtemp(prefix="card-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        game = tmp / "sample"
        game.write_bytes(b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8 + (2).to_bytes(2, "little") + (183).to_bytes(2, "little") + b"\0" * 100)
        staging = tmp / "card"
        staging.mkdir()
        image.populate(staging, variant, game_binary=game, saves=saves if saves is not None else dict(self.SAVES))
        return staging

    def edit(self, staging, old, new):
        path = staging / MANIFEST
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def test_declared_directory_saves_reach_the_card_info(self):
        info = validate_card(DirectorySource(self.staged()))
        self.assertEqual((info.saves_directory, info.saves_units, info.saves_checks, info.saves_max_bytes),
                         (".Sample/save", ["*.sav", "screen-*.zsc"], ["zlib-xml", "zlib"], 8 * 1024 * 1024))
        self.assertEqual(info.saves, "saves")
        plain = validate_card(DirectorySource(self.staged(saves={})))
        self.assertIsNone(plain.saves_directory)

    def test_bad_declarations_are_refused(self):
        cases = {
            'directory = ".Sample/save"': ['directory = "../save"', 'directory = "/root"', 'directory = "a//b"', 'directory = "a b"'],
            'units = ["*.sav", "screen-*.zsc"]': ['units = ["save/*.sav", "x"]', 'units = ["*", "*.zsc"]', 'units = []', 'units = ["[ab]", "x"]'],
            'checks = ["zlib-xml", "zlib"]': ['checks = ["zlib-xml"]', 'checks = ["zlib-xml", "exec"]'],
            "max_bytes = 8388608": ["max_bytes = 8388609", "max_bytes = 10", "max_bytes = true"],
        }
        for old, replacements in cases.items():
            for new in replacements:
                with self.subTest(new):
                    staging = self.staged()
                    self.edit(staging, old, new)
                    with self.assertRaises(CardError):
                        validate_card(DirectorySource(staging))

    def test_all_or_nothing(self):
        staging = self.staged()
        self.edit(staging, "max_bytes = 8388608\n", "")
        with self.assertRaises(CardError) as ctx:
            validate_card(DirectorySource(staging))
        self.assertIn("saves.max_bytes", ctx.exception.detail)

    def test_cli_requires_an_explicit_check(self):
        from mun_card import cli
        tmp = Path(tempfile.mkdtemp(prefix="cardcli-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        game = tmp / "sample"; game.write_bytes(b"\x7fELF")
        with contextlib_quiet():
            code = cli.main(["create", str(tmp / "x.img"), "--variant", "game-gl", "--game", str(game),
                             "--saves-directory", ".Sample/save", "--saves-unit", "*.sav", "--saves-max-bytes", "4096"])
        self.assertNotEqual(code, 0)
        self.assertFalse((tmp / "x.img").exists())


def contextlib_quiet():
    import contextlib, io
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    return stack


class ImageTests(unittest.TestCase):
    def build(self, variant: str, size: int = 8) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="cardimg-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        staging = tmp / "stage"
        staging.mkdir()
        image.populate(staging, variant)
        dest = tmp / f"{variant}.img"
        image.create_image(dest, staging, size)
        return dest

    def test_per_file_hashes_list_every_regular_file(self):
        import hashlib
        from mun_card.source import DebugfsSource
        img = self.build("valid")
        source = DebugfsSource(img, image.find_tool("debugfs"))
        files = dict(source.walk())
        self.assertEqual(set(files), {MANIFEST, "cover.png", "content/README.txt", "content/hello.txt", "content/assets/palette.txt"})
        self.assertEqual(files["content/hello.txt"], len("hola desde la tarjeta\n"))
        digest = hashlib.sha256(source.read("content/hello.txt", files["content/hello.txt"])[:files["content/hello.txt"]]).hexdigest()
        self.assertEqual(digest, hashlib.sha256(b"hola desde la tarjeta\n").hexdigest())
        # The CLI form, with saves/ ignored the way the lab compares before/after play.
        from mun_card import cli
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["hash", str(img), "--files", "--ignore", "saves"])
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 5); self.assertTrue(all(len(l.split("  ")[0]) == 64 for l in lines))

    def test_full_variant_leaves_no_free_block_and_still_validates(self):
        img = self.build("full", size=64)
        self.assertGreater(image.fill_to_capacity(img), 0, "mke2fs leaves a margin; debugfs takes it")
        dumpe2fs = image.find_tool("dumpe2fs")
        text = subprocess.run([dumpe2fs, "-h", str(img)], capture_output=True, text=True).stdout
        free = next(int(l.split(":")[1]) for l in text.splitlines() if l.startswith("Free blocks:"))
        self.assertEqual(free, 0, "a save of any size must hit ENOSPC on this card")
        info = validate_card(DebugfsSource(img, image.find_tool("debugfs")))
        self.assertEqual(info.kind, "test")

    def test_valid_image_is_clean_ext4_and_inspects_unchanged(self):
        dest = self.build("valid")
        before = image.sha256_file(dest)
        fs = ext4.check_mountable(dest)
        self.assertTrue(fs["magic_ok"] and fs["clean"] and not fs["needs_recovery"])
        self.assertEqual(fs["label"], image.CARD_LABEL_PREFIX)
        info = validate_card(DebugfsSource(dest, image.find_tool("debugfs")))
        self.assertEqual(info.id, "mun.testcard")
        self.assertEqual(image.sha256_file(dest), before, "inspection must not modify the image")

    def test_debugfs_source_matches_directory_source_on_defects(self):
        for variant in ("symlink", "missing-file", "cover-too-big", "bad-toml"):
            dest = self.build(variant)
            with self.assertRaises(CardError, msg=variant) as ctx:
                validate_card(DebugfsSource(dest, image.find_tool("debugfs")))
            self.assertEqual(ctx.exception.code,
                             {"symlink": "path_symlink", "missing-file": "path_missing",
                              "cover-too-big": "cover_too_large", "bad-toml": "manifest_syntax"}[variant])

    def test_reproducible_build(self):
        a, b = self.build("valid"), self.build("valid")
        self.assertEqual(image.sha256_file(a), image.sha256_file(b))

    def test_reproducible_build_in_different_seconds(self):
        # A staged file's ctime is when it last changed, and no call sets it
        # back. Staging the second tree in a later second makes any time the
        # image takes from the staging tree or the clock, instead of the
        # fixed epoch, show up as a difference.
        first = self.build("valid")
        staged = [first.parent / "stage", *(first.parent / "stage").rglob("*")]
        last_change = max(os.lstat(path).st_ctime for path in staged)
        time.sleep(max(0.0, int(last_change) + 1.05 - time.time()))
        second = self.build("valid")
        restaged = [second.parent / "stage", *(second.parent / "stage").rglob("*")]
        self.assertGreater(int(min(os.lstat(path).st_ctime for path in restaged)), int(last_change),
                           "the two trees must be staged in different seconds")
        self.assertEqual(image.sha256_file(first), image.sha256_file(second))

    def test_a_debugfs_complaint_fails_the_build_and_leaves_no_image(self):
        # debugfs exits 0 when one of its requests fails; what it prints on
        # stderr besides its version line is the only sign.
        tmp = Path(tempfile.mkdtemp(prefix="cardimg-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "debugfs"
        fake.write_text('#!/bin/sh\necho "debugfs 1.47.0 (5-Feb-2023)" >&2\n'
                        'echo "<13>: File not found by ext2_lookup" >&2\n')
        fake.chmod(0o755)
        staging = tmp / "stage"
        staging.mkdir()
        image.populate(staging, "valid")
        real = image.find_tool
        with unittest.mock.patch.object(image, "find_tool", lambda name: str(fake) if name == "debugfs" else real(name)):
            with self.assertRaises(CardError) as ctx:
                image.create_image(tmp / "x.img", staging, 8)
        self.assertEqual(ctx.exception.code, "debugfs_failed")
        self.assertIn("File not found", ctx.exception.detail)
        self.assertEqual(sorted(path.name for path in tmp.iterdir()), ["debugfs", "stage"])

    def test_non_ext4_and_recovery_flag_are_rejected(self):
        tmp = Path(tempfile.mkdtemp(prefix="cardimg-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        raw = tmp / "raw.img"
        raw.write_bytes(b"\0" * (4 * 1024 * 1024))
        with self.assertRaises(CardError) as ctx:
            ext4.check_mountable(raw)
        self.assertEqual(ctx.exception.code, "image_not_ext4")
        dest = self.build("valid")
        # Set the needs_recovery incompat bit as an unclean shutdown would leave it.
        with open(dest, "r+b") as handle:
            handle.seek(1024 + 0x60)
            flags = int.from_bytes(handle.read(4), "little") | ext4.INCOMPAT_RECOVER
            handle.seek(1024 + 0x60)
            handle.write(flags.to_bytes(4, "little"))
        with self.assertRaises(CardError) as ctx:
            ext4.check_mountable(dest)
        self.assertEqual(ctx.exception.code, "image_needs_recovery")

    def test_partitioned_image_is_rejected(self):
        tmp = Path(tempfile.mkdtemp(prefix="cardimg-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        disk = tmp / "disk.img"
        data = bytearray(4 * 1024 * 1024)
        data[446:462] = bytes([0x00, 0x20, 0x21, 0x00, 0x83, 0x2A, 0x2B, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00, 0x18, 0x00, 0x00])
        data[510:512] = b"\x55\xaa"
        disk.write_bytes(bytes(data))
        with self.assertRaises(CardError) as ctx:
            ext4.check_mountable(disk)
        self.assertEqual(ctx.exception.code, "image_partitioned")

    def test_cli_inspect_exit_codes(self):
        valid, broken = self.build("valid"), self.build("bad-schema")
        tool = ROOT / "tools" / "mun-card" / "mun-card"
        self.assertEqual(subprocess.run([sys.executable, str(tool), "inspect", str(valid)], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([sys.executable, str(tool), "inspect", str(broken)], capture_output=True).returncode, 2)


class NamingGenerationTests(unittest.TestCase):
    """Naming generations: the manifest's name tells the two apart."""

    def staged(self, naming="mun", variant="valid") -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="card-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        staging = tmp / "card"; staging.mkdir()
        image.populate(staging, variant, naming=naming)
        return staging

    def test_both_generations_validate_and_say_which_they_are(self):
        for naming, name in (("mun", "mun.toml"), ("earlier", "neptune.toml")):
            with self.subTest(naming=naming):
                staging = self.staged(naming)
                self.assertEqual(sorted(p.name for p in staging.glob("*.toml")), [name])
                self.assertEqual(validate_card(DirectorySource(staging)).naming, naming)

    def test_both_names_at_the_root_are_refused_whatever_either_holds(self):
        for other in ("same text", "broken", "symlink", "directory"):
            with self.subTest(other=other):
                staging = self.staged("mun")
                earlier = staging / "neptune.toml"
                if other == "same text":
                    earlier.write_text((staging / MANIFEST).read_text())
                elif other == "broken":
                    earlier.write_text("[card\n")
                elif other == "symlink":
                    os.symlink("/etc/hostname", earlier)
                else:
                    earlier.mkdir()
                with self.assertRaises(CardError) as ctx:
                    validate_card(DirectorySource(staging))
                self.assertEqual(ctx.exception.code, "manifest_ambiguous")
        with self.assertRaises(CardError) as ctx:
            validate_card(DirectorySource(self.staged("mun", "ambiguous-manifest")))
        self.assertEqual(ctx.exception.code, "manifest_ambiguous")

    def test_the_save_format_follows_the_generation(self):
        from mun_card.validate import save_format
        self.assertEqual(save_format({"naming": "mun"}), "mun-save/1")
        self.assertEqual(save_format({"naming": "earlier"}), "neptune-save/1")
        self.assertEqual(save_format({}), "neptune-save/1", "a card described without a naming generation")

    @unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
    def test_new_cards_have_mun_names_unless_asked_for_the_earlier_ones(self):
        from mun_card import cli
        with tempfile.TemporaryDirectory() as tmp:
            for args, name in (([], "mun.toml"), (["--earlier-names"], "neptune.toml")):
                img = Path(tmp) / f"x{len(args)}.img"
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["create", str(img), "--size", "8"] + args), 0)
                source = DebugfsSource(img, image.find_tool("debugfs"))
                self.assertIsNotNone(source.stat(name), args)
                self.assertEqual(validate_card(source).naming, "mun" if not args else "earlier")


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
class ConvertTests(unittest.TestCase):
    """mun-card convert (docs/game-cards.md): a verified copy with MUN names; the source never changes."""

    COMPATIBLE = b"\x7fELF built with MUN_%s NEPTUNE_%s mun-save/1 neptune-save/1"

    def setUp(self):
        from mun_card import convert
        self.convert = convert
        self.base = Path(tempfile.mkdtemp(prefix="convert-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "gamecards"; self.root.mkdir()

    def envelope(self, n, fmt="neptune-save/1", game="mun.collect", **extra):
        return (json.dumps({"format": fmt, "game": game, "content_version": "0.1.0", "schema": 1,
                            "payload": {"n": n}, **extra}, ensure_ascii=False) + "\n").encode()

    def card(self, name="old", naming="earlier", saves=None, executable=None, variant="game", extra=None, prepare=None):
        game = self.base / "mun-collect"; game.write_bytes(executable if executable is not None else self.COMPATIBLE)
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "c"; staging.mkdir()
            image.populate(staging, variant, "Prueba", game if variant.startswith("game") else None, naming=naming)
            save_dir = staging / "saves" / "mun.collect"
            for file_name, data in (saves if saves is not None else {"save.json": self.envelope(3),
                                                                     "save.json.prev": self.envelope(2)}).items():
                save_dir.mkdir(parents=True, exist_ok=True)
                (save_dir / file_name).write_bytes(data)
            for relative, data in (extra or {}).items():
                (staging / relative).parent.mkdir(parents=True, exist_ok=True)
                (staging / relative).write_bytes(data)
            if prepare:
                prepare(staging)
            image.create_image(self.root / f"{name}.img", staging, 8)
        return self.root / f"{name}.img"

    def directory_card(self, name, document):
        """An earlier-generation card with directory saves whose save.json holds `document`."""
        game = self.base / "sample"; game.write_bytes(b"\x7fELF /run/mun/card/content")
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "c"; staging.mkdir()
            image.populate(staging, "game-gl", "Sample", game, naming="earlier",
                           saves={"directory": ".Sample/save", "units": ["*.sav"], "checks": ["zlib-xml"],
                                  "max_bytes": 4096})
            card_id = validate_card(DirectorySource(staging)).id
            (staging / "saves" / card_id).mkdir(parents=True)
            raw = document(card_id) if callable(document) else document
            (staging / "saves" / card_id / "save.json").write_bytes(raw)
            image.create_image(self.root / f"{name}.img", staging, 8)
        return self.root / f"{name}.img"

    def files_document(self, card_id, files, **over):
        import base64, hashlib
        document = {"format": "neptune-save/1", "game": card_id, "schema": 1, "payload_kind": "files",
                    "payload": {"files": [{"path": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest(),
                                           "data": base64.b64encode(d).decode()} for n, d in files.items()]}}
        document.update(over)
        for key in [k for k, v in over.items() if v is None]:
            del document[key]
        return (json.dumps(document) + "\n").encode()

    def run_convert(self, source, name="new", stated=True):
        return self.convert.convert(source, self.root / f"{name}.img", self.root, stated)

    def files(self, img):
        source = DebugfsSource(img, image.find_tool("debugfs"))
        return {relative: source.read(relative, size)[:size] for relative, size in source.walk()}

    def assert_nothing_left(self, source, before):
        self.assertEqual(image.sha256_file(source), before, "the source never changes")
        self.assertFalse((self.root / "new.img").exists())
        self.assertFalse([p for p in self.root.iterdir() if ".converting-" in p.name], "no temporary file left")
        registry = json.loads((self.root / "attached.json").read_text()) if (self.root / "attached.json").exists() else {}
        self.assertEqual(registry, {}, "the reservation is released")

    def test_only_the_manifest_name_and_the_save_formats_change(self):
        source = self.card(extra={"saves/mun.collect/save.json.damaged-20260101T000000Z": b"not json {",
                                  "saves/other.game/save.json": self.envelope(9, game="other.game")})
        before = image.sha256_file(source)
        result = self.run_convert(source)
        self.assertEqual(image.sha256_file(source), before)
        self.assertEqual(result["saves_converted"], ["save.json", "save.json.prev"])
        old, new = self.files(source), self.files(self.root / "new.img")
        self.assertEqual(new.pop("mun.toml"), old.pop("neptune.toml"), "renamed with identical bytes")
        for name, n in (("save.json", 3), ("save.json.prev", 2)):
            path = f"saves/mun.collect/{name}"
            self.assertEqual(json.loads(new.pop(path)), dict(json.loads(old.pop(path)), format="mun-save/1"))
            self.assertEqual(json.loads(self.files(self.root / "new.img")[path])["payload"], {"n": n})
        self.assertEqual(new, old, "every other file byte-identical, damaged saves and other games' saves included")
        self.assertEqual(validate_card(DebugfsSource(self.root / "new.img", image.find_tool("debugfs"))).naming, "mun")
        self.assertEqual(result["game_compatibility"], "declared, not verified")
        self.assertEqual(json.loads((self.root / "attached.json").read_text()), {})

    def test_a_directory_save_card_converts_with_its_payload_intact(self):
        import base64, hashlib
        data = b"x" * 100
        payload = {"files": [{"path": "save-0000.sav", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "data": base64.b64encode(data).decode()}]}
        doc = lambda p: (json.dumps({"format": "neptune-save/1", "game": "mun.sample", "schema": 1,
                                     "payload_kind": "files", "payload": p}) + "\n").encode()
        game = self.base / "sample"; game.write_bytes(b"\x7fELF /run/mun/card/content")
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "c"; staging.mkdir()
            image.populate(staging, "game-gl", "Sample", game, naming="earlier",
                           saves={"directory": ".Sample/save", "units": ["*.sav"], "checks": ["any"], "max_bytes": 4096})
            info = validate_card(DirectorySource(staging))
            (staging / "saves" / info.id).mkdir(parents=True)
            (staging / "saves" / info.id / "save.json").write_bytes(doc(payload))
            image.create_image(self.root / "aq.img", staging, 8)
        result = self.run_convert(self.root / "aq.img")
        self.assertEqual(result["saves_converted"], ["save.json"])
        # A damaged payload is recovered on another copy first, never carried.
        bad = dict(payload, files=[dict(payload["files"][0], sha256="0" * 64)])
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "c"; staging.mkdir()
            image.populate(staging, "game-gl", "Sample", game, naming="earlier",
                           saves={"directory": ".Sample/save", "units": ["*.sav"], "checks": ["any"], "max_bytes": 4096})
            (staging / "saves" / info.id).mkdir(parents=True)
            (staging / "saves" / info.id / "save.json").write_bytes(doc(bad))
            image.create_image(self.root / "aqbad.img", staging, 8)
        with self.assertRaises(CardError) as ctx:
            self.run_convert(self.root / "aqbad.img", "aqbadnew")
        self.assertEqual(ctx.exception.code, "save_damaged")

    def test_refusals_leave_the_source_as_it_was_and_no_destination(self):
        cases = {
            "damaged save.json": (lambda: self.card(saves={"save.json": b"not json"}), "save_damaged"),
            "damaged save.json.prev": (lambda: self.card(saves={"save.json": self.envelope(1), "save.json.prev": b"{"}), "save_damaged"),
            "save of the other generation": (lambda: self.card(saves={"save.json": self.envelope(1, "mun-save/1")}), "save_damaged"),
            "another game's save in the card's place": (lambda: self.card(saves={"save.json": self.envelope(1, game="x.y")}), "save_damaged"),
            "already MUN names": (lambda: self.card(naming="mun"), "already_mun"),
            "invalid card": (lambda: self.card(variant="bad-arch", saves={}), "arch_unsupported"),
            "reads only neptune-save/1": (lambda: self.card(executable=b"\x7fELF MUN_%s neptune-save/1"), "game_earlier_only"),
            "reads only NEPTUNE_": (lambda: self.card(executable=b"\x7fELF NEPTUNE_%s mun-save/1"), "game_earlier_only"),
            "content only at /run/neptune/card": (lambda: self.card(executable=b"\x7fELF MUN_ mun-save/1 /run/neptune/card/content"),
                                                  "game_earlier_only"),
        }
        for what, (make, code) in cases.items():
            with self.subTest(what):
                for leftover in self.root.iterdir():
                    leftover.unlink()
                source = make(); before = image.sha256_file(source)
                with self.assertRaises(CardError) as ctx:
                    self.run_convert(source)
                self.assertEqual(ctx.exception.code, code)
                self.assert_nothing_left(source, before)

    # Review of the implementation, R1: what a save is, and whether it exists,
    # is decided from the image, never from the host extraction.

    def test_a_special_file_where_a_save_would_be_is_not_an_absent_save(self):
        for name in ("save.json", "save.json.prev"):
            with self.subTest(name=name):
                for leftover in self.root.iterdir():
                    leftover.unlink()
                def fifo(staging, name=name):
                    save_dir = staging / "saves" / "mun.collect"
                    save_dir.mkdir(parents=True, exist_ok=True)
                    os.mkfifo(save_dir / name)
                saves = {"save.json": self.envelope(3)} if name == "save.json.prev" else {}
                source = self.card(saves=saves, prepare=fifo); before = image.sha256_file(source)
                self.assertEqual(DebugfsSource(source, image.find_tool("debugfs")).stat(f"saves/mun.collect/{name}").kind, "other")
                with self.assertRaises(CardError) as ctx:
                    self.run_convert(source)
                self.assertEqual(ctx.exception.code, "special_file")
                self.assert_nothing_left(source, before)

    def test_a_special_file_anywhere_on_the_card_is_refused(self):
        source = self.card(prepare=lambda staging: os.mkfifo(staging / "content" / "pipe")); before = image.sha256_file(source)
        with self.assertRaises(CardError) as ctx:
            self.run_convert(source)
        self.assertEqual((ctx.exception.code, ctx.exception.detail), ("special_file", "content/pipe"))
        self.assert_nothing_left(source, before)

    def test_a_link_in_the_save_path_is_refused_before_any_host_file_is_read(self):
        host = self.base / "host-sentinel"; host.mkdir()
        sentinel = self.envelope(9); (host / "save.json").write_bytes(sentinel)
        def link(staging):
            (staging / "saves").mkdir(exist_ok=True)
            (staging / "saves" / "mun.collect").symlink_to(host, target_is_directory=True)
        source = self.card(saves={}, prepare=link); before = image.sha256_file(source)
        reads, real_read, real_open = [], Path.read_bytes, os.open
        def observed_read(path):
            if Path(path).resolve() == (host / "save.json").resolve():
                reads.append(str(path))
            return real_read(path)
        def observed_open(path, *args, **kwargs):
            if Path(path).resolve() == (host / "save.json").resolve():
                reads.append(str(path))
            return real_open(path, *args, **kwargs)
        with unittest.mock.patch.object(Path, "read_bytes", observed_read), \
                unittest.mock.patch.object(os, "open", observed_open):
            with self.assertRaises(CardError) as ctx:
                self.run_convert(source)
        self.assertEqual(ctx.exception.code, "save_damaged")
        self.assertEqual(reads, [], "the card's link never leads to a read on the host")
        self.assertEqual((host / "save.json").read_bytes(), sentinel)
        self.assert_nothing_left(source, before)

    # R2: a save is carried only if the console would restore it.

    def test_a_save_the_console_would_not_restore_is_refused(self):
        slot = zlib.compress(b"<Save a='1'/>")
        cases = {
            "directory save without its kind": lambda cid: self.files_document(cid, {"a.sav": slot}, payload_kind=None),
            "directory save without its kind, broken files": lambda cid: (json.dumps(
                {"format": "neptune-save/1", "game": cid, "schema": 1, "payload": {"files": "broken"}}) + "\n").encode(),
            "directory save of another schema": lambda cid: self.files_document(cid, {"a.sav": slot}, schema=99),
            "a file that is not a declared unit": lambda cid: self.files_document(cid, {"a.zsc": slot}),
            "a unit that fails its check": lambda cid: self.files_document(cid, {"a.sav": b"not zlib"}),
        }
        for what, document in cases.items():
            with self.subTest(what):
                for leftover in self.root.iterdir():
                    leftover.unlink()
                source = self.directory_card("old", document); before = image.sha256_file(source)
                with self.assertRaises(CardError) as ctx:
                    self.run_convert(source)
                self.assertEqual(ctx.exception.code, "save_damaged")
                self.assert_nothing_left(source, before)
        for leftover in self.root.iterdir():
            leftover.unlink()
        self.run_convert(self.directory_card("old", lambda cid: self.files_document(cid, {"a.sav": slot})))
        self.assertTrue((self.root / "new.img").exists(), "the valid directory save still converts")
        single = {
            "a files kind on a single-object card": self.envelope(1, payload_kind="files"),
            "a schema that is not a save schema": self.envelope(1, schema=0),
            "a payload that is not an object": (json.dumps({"format": "neptune-save/1", "game": "mun.collect",
                                                            "schema": 1, "payload": [1, 2]}) + "\n").encode(),
        }
        for what, raw in single.items():
            with self.subTest(what):
                for leftover in self.root.iterdir():
                    leftover.unlink()
                source = self.card(saves={"save.json": raw}); before = image.sha256_file(source)
                with self.assertRaises(CardError) as ctx:
                    self.run_convert(source)
                self.assertEqual(ctx.exception.code, "save_damaged")
                self.assert_nothing_left(source, before)

    def test_without_the_statement_nothing_is_converted(self):
        source = self.card(); before = image.sha256_file(source)
        with self.assertRaises(CardError) as ctx:
            self.run_convert(source, stated=False)
        self.assertEqual(ctx.exception.code, "compatibility_unstated")
        self.assert_nothing_left(source, before)

    def test_an_existing_destination_or_an_unclean_source_is_refused(self):
        source = self.card(); before = image.sha256_file(source)
        (self.root / "new.img").write_bytes(b"someone else's card")
        with self.assertRaises(CardError) as ctx:
            self.run_convert(source)
        self.assertEqual(ctx.exception.code, "image_exists")
        self.assertEqual((self.root / "new.img").read_bytes(), b"someone else's card")
        (self.root / "new.img").unlink()
        unclean = self.root / "unclean.img"; shutil.copyfile(source, unclean)
        with open(unclean, "r+b") as handle:          # mark the filesystem as having errors
            handle.seek(ext4.SUPERBLOCK_OFFSET + 0x3A); state = int.from_bytes(handle.read(2), "little")
            handle.seek(ext4.SUPERBLOCK_OFFSET + 0x3A); handle.write((state | ext4.STATE_ERRORS).to_bytes(2, "little"))
        unclean_before = image.sha256_file(unclean)
        with self.assertRaises(CardError) as ctx:
            self.run_convert(unclean)
        self.assertEqual(ctx.exception.code, "image_has_errors")
        self.assertEqual(image.sha256_file(unclean), unclean_before, "never repaired")
        with self.assertRaises(CardError) as ctx:
            self.convert.convert(source, self.base / "elsewhere.img", self.root, True)
        self.assertEqual(ctx.exception.code, "image_path")
        self.assertEqual(image.sha256_file(source), before)

    def test_a_card_held_by_a_console_is_refused(self):
        source = self.card(); before = image.sha256_file(source)
        (self.root / "attached.json").write_text(json.dumps({"old": {"instance": "a", "pid": os.getppid()}}))
        with self.assertRaises(CardError) as ctx:
            self.run_convert(source)
        self.assertEqual(ctx.exception.code, "card_in_use")
        self.assertEqual(json.loads((self.root / "attached.json").read_text()), {"old": {"instance": "a", "pid": os.getppid()}})
        self.assertEqual(image.sha256_file(source), before)
        (self.root / "attached.json").write_text(json.dumps({"old": {"instance": "a", "pid": 2 ** 22 + 7}}))
        self.run_convert(source)                        # a dead holder is stale, as for the lab tool
        self.assertTrue((self.root / "new.img").exists())

    def test_an_attach_during_the_conversion_is_refused(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("munvm_for_convert", ROOT / "vm" / "munvm.py")
        vm = importlib.util.module_from_spec(spec); spec.loader.exec_module(vm)
        vm.ATTACH_REGISTRY = self.root / "attached.json"
        vm.INSTANCE = "a"
        vm.read_pid = lambda: os.getpid() + 1
        refused = []
        original = self.convert._eligibility
        def attach_meanwhile(*args, **kwargs):
            for name in ("old", "new"):
                with self.assertRaises(vm.LabError) as ctx:
                    vm.registry_claim(name)
                refused.append(str(ctx.exception))
            return original(*args, **kwargs)
        source = self.card()
        with unittest.mock.patch.object(self.convert, "_eligibility", attach_meanwhile):
            self.run_convert(source)
        self.assertEqual(len(refused), 2)
        self.assertTrue(all("reserved by a card conversion" in message for message in refused), refused)
        vm.registry_claim("old")                        # afterwards the card can be attached again
        self.assertEqual(json.loads((self.root / "attached.json").read_text())["old"]["instance"], "a")

    def test_a_failure_while_copying_or_verifying_leaves_nothing(self):
        failures = {
            "copy": ("shutil.copyfile", OSError("disk full")),
            "verification": ("_tree", CardError("convert_failed", "simulated")),
        }
        for what, (target, error) in failures.items():
            with self.subTest(what):
                for leftover in self.root.iterdir():
                    leftover.unlink()
                source = self.card(); before = image.sha256_file(source)
                if target.startswith("shutil."):
                    patcher = unittest.mock.patch.object(self.convert.shutil, "copyfile", side_effect=error)
                else:
                    patcher = unittest.mock.patch.object(self.convert, target, side_effect=error)
                with patcher, self.assertRaises(type(error)):
                    self.run_convert(source)
                self.assert_nothing_left(source, before)

    def test_a_destination_that_appears_meanwhile_is_not_replaced(self):
        source = self.card(); before = image.sha256_file(source)
        original = self.convert._convert_reserved
        def racing(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / "new.img").write_bytes(b"appeared")
            return result
        with unittest.mock.patch.object(self.convert, "_convert_reserved", racing):
            with self.assertRaises(CardError) as ctx:
                self.run_convert(source)
        self.assertEqual(ctx.exception.code, "image_exists")
        self.assertEqual((self.root / "new.img").read_bytes(), b"appeared")
        self.assertEqual(image.sha256_file(source), before)
        self.assertFalse([p for p in self.root.iterdir() if ".converting-" in p.name])


if __name__ == "__main__":
    unittest.main()
