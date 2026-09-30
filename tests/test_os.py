"""Host checks of the MUN OS image composition (os/) and of the staging split of
the service installers. No builder, mkosi or QEMU is needed; the image build
itself is exercised in a builder VM (os/README.md)."""

import hashlib
import importlib.util
import json
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OS = ROOT / "os"
MKOSI = OS / "mkosi"
PROFILE = MKOSI / "mkosi.profiles" / "qemu-dev" / "mkosi.conf"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inputs_tool = load("mun_inputs", OS / "builder" / "inputs.py")
build_info = load("mun_build_info", OS / "builder" / "build_info.py")


def mkosi_list(path, key):
    """The values of a multi-line mkosi list setting, comments dropped."""
    values, active = [], False
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if re.match(rf"^{key}=", line):
            active = True
            values += line.split("=", 1)[1].split()
            continue
        if active and line.startswith((" ", "\t")):
            values += line.split()
        elif active and line.strip():
            active = False
        elif active and not raw.strip():
            active = False
    return values


def package_file(path):
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


class CompositionTests(unittest.TestCase):
    def test_every_service_runtime_package_is_in_the_image(self):
        image = set(mkosi_list(MKOSI / "mkosi.conf", "Packages"))
        for path in sorted(ROOT.glob("services/*/deploy/*packages")):
            if path.name == "build-packages":
                continue
            missing = set(package_file(path)) - image
            self.assertFalse(missing, f"{path.relative_to(ROOT)} lists packages the image lacks: {sorted(missing)}")

    def test_every_service_build_package_is_in_the_build_overlay(self):
        overlay = set(mkosi_list(MKOSI / "mkosi.conf", "BuildPackages"))
        for path in sorted(ROOT.glob("services/*/deploy/build-packages")):
            missing = set(package_file(path)) - overlay
            self.assertFalse(missing, f"{path.relative_to(ROOT)}: {sorted(missing)} missing from BuildPackages")

    def test_compilers_stay_out_of_the_image(self):
        image = set(mkosi_list(MKOSI / "mkosi.conf", "Packages")) | set(mkosi_list(PROFILE, "Packages"))
        for tool in ("gcc", "g++", "cmake", "make", "qt6-base-dev", "libsdl2-dev"):
            self.assertNotIn(tool, image)

    def test_the_qemu_profile_says_it_is_a_development_build(self):
        text = PROFILE.read_text()
        self.assertIn("MUN_ENVIRONMENT=qemu-arm64", text)
        self.assertIn("MUN_RELEASE=false", text)
        self.assertNotIn("MUN_RELEASE=true", (MKOSI / "mkosi.conf").read_text())

    def test_settings_sit_in_the_sections_mkosi_expects(self):
        # mkosi warns, and a later version may refuse, when a setting is in
        # another section: Environment= belongs to [Build].
        for path in (MKOSI / "mkosi.conf", PROFILE):
            section = None
            for line in path.read_text().splitlines():
                if re.match(r"^\[\w+\]$", line):
                    section = line
                elif line.startswith("Environment="):
                    self.assertEqual(section, "[Build]", path)

    def test_development_access_is_only_in_the_development_profile(self):
        # Everything the profile adds beyond booting (the kernel and systemd-boot)
        # is development access or tooling, and none of it is common.
        boot = {"linux-image-arm64", "systemd-boot-efi"}
        development = set(mkosi_list(PROFILE, "Packages")) - boot
        self.assertTrue(any(name.startswith("qemu-guest") for name in development), "qemu-ga comes with the profile")
        self.assertFalse(development & set(mkosi_list(MKOSI / "mkosi.conf", "Packages")))
        common = (MKOSI / "mkosi.conf").read_text() + (MKOSI / "mkosi.postinst.chroot").read_text()
        for access in ("qemu-guest", "openssh-server", "RootPassword", "Autologin", "Ssh="):
            self.assertNotIn(access, common)
        self.assertNotIn("RootPassword", PROFILE.read_text())
        self.assertNotIn("Autologin", PROFILE.read_text())

    def test_the_image_boots_quietly(self):
        # Firmware, then a blank display, then the shell's splash.
        cmdline = mkosi_list(PROFILE, "KernelCommandLine")
        for parameter in ("quiet", "loglevel=3", "vt.global_cursor_default=0"):
            self.assertIn(parameter, cmdline)
        self.assertIn("systemd.firstboot=off", cmdline, "first boot must never wait for an answer")

    def test_the_console_services_are_enabled_and_tty1_left_to_the_shell(self):
        preset = (MKOSI / "mkosi.extra" / "usr" / "lib" / "systemd" / "system-preset" / "10-mun.preset").read_text()
        for unit in ("run-mun-launch.mount", "mun-cardd.service", "mun-launchd.service",
                     "mun-shell.service"):
            self.assertIn(f"enable {unit}", preset)
        self.assertIn("disable getty@.service", preset)

    def test_build_scripts_never_have_network(self):
        text = (MKOSI / "mkosi.conf").read_text()
        self.assertRegex(text, r"(?m)^WithNetwork=no$")
        for script in ("mkosi.prepare.chroot", "mkosi.build.chroot", "mkosi.postinst.chroot"):
            body = (MKOSI / script).read_text()
            for fetcher in ("git clone", "curl ", "wget ", "apt-get", "pip "):
                self.assertNotIn(fetcher, body, f"{script} must not fetch anything")

    def test_scripts_are_executable(self):
        for path in [*MKOSI.glob("mkosi.*.chroot"), *OS.glob("builder/*"),
                     *ROOT.glob("services/*/deploy/*.sh"), ROOT / "mun"]:
            if path.suffix in (".sh", ".py", ".chroot", "") and path.is_file():
                self.assertTrue(path.stat().st_mode & stat.S_IXUSR, f"{path.relative_to(ROOT)} is not executable")


class ShellTests(unittest.TestCase):
    SHELL = ROOT / "services" / "mun-shell"

    def test_the_player_settings_live_in_the_state_directory(self):
        unit = (self.SHELL / "deploy" / "mun-shell.service").read_text()
        self.assertRegex(unit, r"(?m)^StateDirectory=mun-shell$")
        self.assertIn('qEnvironmentVariable("STATE_DIRECTORY")', (self.SHELL / "src" / "shellsettings.cpp").read_text())

    def test_a_new_resolution_restarts_the_shell_cleanly(self):
        # The shell exits with its restart status to open the display in a new
        # mode; the unit must start it again at once and not count a failure.
        header = (self.SHELL / "src" / "displaymode.h").read_text()
        status = re.search(r"kRestartStatus = (\d+);", header).group(1)
        unit = (self.SHELL / "deploy" / "mun-shell.service").read_text()
        self.assertRegex(unit, rf"(?m)^RestartForceExitStatus={status}$")
        success = re.search(r"(?m)^SuccessExitStatus=(.+)$", unit).group(1).split()
        self.assertIn(status, success)
        self.assertIn("1", success, "Qt's VT handler exits 1 after SIGTERM: a stop, not a failure")

    def test_every_offered_resolution_has_a_modeline_of_its_size(self):
        # A virtual connector gets each mode as a modeline: its displayed size
        # must be the mode's, or the canvas would be scaled for another one.
        source = (self.SHELL / "src" / "displaymode.cpp").read_text()
        modes = re.findall(r'QStringLiteral\("(\d+x\d+)"\)', source.split("kModes{", 1)[1].split("};", 1)[0])
        self.assertEqual(modes, ["1280x720", "1920x1080", "2560x1440"])
        lines = dict(re.findall(r'\{QStringLiteral\("(\d+x\d+)"\), QStringLiteral\("([^"]+)"\)\}', source))
        for mode in modes:
            fields = lines[mode].split()
            self.assertEqual(len(fields), 11, mode)
            width, height = (int(n) for n in mode.split("x"))
            hdisp, hss, hse, htot, vdisp, vss, vse, vtot = (int(n) for n in fields[1:9])
            self.assertEqual((hdisp, vdisp), (width, height), mode)
            self.assertTrue(hdisp < hss < hse < htot and vdisp < vss < vse < vtot, mode)
            refresh = float(fields[0]) * 1e6 / (htot * vtot)
            self.assertAlmostEqual(refresh, 60, delta=0.5, msg=mode)
            self.assertIn(fields[9], ("+hsync", "-hsync"))
            self.assertIn(fields[10], ("+vsync", "-vsync"))

    def test_the_interface_sounds_are_plain_pcm_and_all_accounted_for(self):
        # What the player (src/systemsounds.cpp) accepts, and nothing but the
        # samples: authoring tools add chunks (metadata, provenance) that have
        # no place in the console.
        sounds = sorted((self.SHELL / "sounds").glob("*.wav"))
        self.assertEqual([p.stem for p in sounds], ["back", "enter", "move"])
        cmake = (self.SHELL / "CMakeLists.txt").read_text()
        player = (self.SHELL / "src" / "systemsounds.cpp").read_text()
        for path in sounds:
            data = path.read_bytes()
            self.assertEqual((data[:4], data[8:12]), (b"RIFF", b"WAVE"), path.name)
            chunks, at = [], 12
            while at < len(data):
                size = int.from_bytes(data[at + 4:at + 8], "little")
                chunks.append((data[at:at + 4], data[at + 8:at + 8 + size]))
                at += 8 + size + (size & 1)
            self.assertEqual([name for name, _ in chunks], [b"fmt ", b"data"], f"{path.name}: only the format and the samples")
            fmt, samples = chunks[0][1], chunks[1][1]
            tag, channels, rate = int.from_bytes(fmt[0:2], "little"), int.from_bytes(fmt[2:4], "little"), int.from_bytes(fmt[4:8], "little")
            self.assertEqual((tag, channels, rate, int.from_bytes(fmt[14:16], "little")), (1, 2, 48000, 16), path.name)
            self.assertLessEqual(len(samples) / (48000 * 4), 1.0, f"{path.name}: a menu sound lasts under a second")
            self.assertIn(f"sounds/{path.name}", cmake, "compiled in")
            self.assertIn(f'"{path.stem}"', player, "loaded by the player")
        played = set(re.findall(r'sound\("([a-z]+)"\)', (self.SHELL / "qml" / "Main.qml").read_text()))
        self.assertEqual(played, {p.stem for p in sounds}, "every sound the menus ask for exists, and every one is used")
        unit = (self.SHELL / "deploy" / "mun-shell.service").read_text()
        self.assertRegex(unit, r"(?m)^SupplementaryGroups=.*\baudio\b", "the shell may open the sound device")

    def test_the_lab_display_describes_the_shells_three_modes(self):
        # The virtual display's EDID (os/builder/lab_edid.py): what the
        # initrd carries is the generator's output, a valid EDID 1.3 block
        # whose detailed timings are exactly the shell's modes, 1080p first
        # (preferred), each at 60 Hz.
        spec = importlib.util.spec_from_file_location("lab_edid", ROOT / "os" / "builder" / "lab_edid.py")
        lab_edid = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lab_edid)
        shipped = ROOT / "os/mkosi/mkosi.images/initrd/mkosi.profiles/qemu-dev/extra/usr/lib/firmware/edid/mun-lab.bin"
        block = shipped.read_bytes()
        self.assertEqual(block, lab_edid.edid(), "regenerate it with os/builder/lab_edid.py")
        self.assertEqual((len(block), block[:8], sum(block) % 256), (128, bytes.fromhex("00ffffffffffff00"), 0))
        self.assertTrue(block[24] & 0x02, "the first detailed timing is the preferred mode")
        modes = []
        for at in (54, 72, 90):
            d = block[at:at + 18]
            clock = int.from_bytes(d[0:2], "little") * 10_000
            ha, hb = d[2] | (d[4] >> 4) << 8, d[3] | (d[4] & 0x0F) << 8
            va, vb = d[5] | (d[7] >> 4) << 8, d[6] | (d[7] & 0x0F) << 8
            self.assertAlmostEqual(clock / ((ha + hb) * (va + vb)), 60, delta=0.1)
            modes.append(f"{ha}x{va}")
        self.assertEqual(modes, ["1920x1080", "2560x1440", "1280x720"])
        source = (self.SHELL / "src" / "displaymode.cpp").read_text()
        offered = re.findall(r'QStringLiteral\("(\d+x\d+)"\)', source.split("kModes{", 1)[1].split("};", 1)[0])
        self.assertEqual(sorted(modes), sorted(offered), "the lab display takes every mode the shell offers")
        profile = (ROOT / "os/mkosi/mkosi.profiles/qemu-dev/mkosi.conf").read_text()
        self.assertIn("drm.edid_firmware=Virtual-1:edid/mun-lab.bin", profile)

    def test_the_launcher_may_open_the_display_card_and_nothing_else(self):
        # DisplayHold (launchd.py) keeps the console's mode for a game: one
        # device, and no capability to master a display someone else holds.
        unit = (ROOT / "services/mun-launchd/deploy/mun-launchd.service").read_text()
        self.assertRegex(unit, r"(?m)^DevicePolicy=closed$")
        self.assertEqual(re.findall(r"(?m)^DeviceAllow=(.+)$", unit), ["/dev/dri/card0 rw"])
        capabilities = re.search(r"(?m)^CapabilityBoundingSet=(.+)$", unit).group(1).split()
        self.assertNotIn("CAP_SYS_ADMIN", capabilities)

    def test_every_compiled_in_font_carries_its_licence(self):
        cmake = (self.SHELL / "CMakeLists.txt").read_text()
        fonts = sorted((self.SHELL / "fonts").glob("*.ttf"))
        self.assertTrue(fonts)
        for font in fonts:
            self.assertIn(f"fonts/{font.name}", cmake, "the font is compiled in")
            family = font.stem.split("-")[0]
            licence = self.SHELL / "fonts" / f"{family}-OFL.txt"
            self.assertTrue(licence.is_file(), f"{font.name} needs {licence.name}")
            self.assertIn("SIL Open Font License", licence.read_text())

    def test_text_from_cards_is_never_read_as_markup(self):
        # Card titles and service messages reach the screen; only the two text
        # components may create a Text, and both are plain text.
        for name in ("UiText.qml", "MarkText.qml"):
            self.assertIn("textFormat: Text.PlainText", (self.SHELL / "qml" / name).read_text())
        for path in sorted((self.SHELL / "qml").glob("*.qml")):
            if path.name in ("UiText.qml", "MarkText.qml"):
                continue
            self.assertNotRegex(path.read_text(), r"(?m)^\s*Text\s*\{", path.name)


class IdentityTests(unittest.TestCase):
    def test_the_shell_shows_mun_and_takes_version_and_stage_from_the_image(self):
        shell = ROOT / "services" / "mun-shell"
        for path in [*shell.glob("qml/*.qml"), *shell.glob("src/*")]:
            self.assertNotIn("Neptune", path.read_text(), path.name)
        about = (shell / "qml" / "Panels.qml").read_text()
        for fact in ("SystemInfo.osTitle", "SystemInfo.environment", "SystemInfo.release"):
            self.assertIn(fact, about, "About this console names the system as the image does")
        self.assertIn("/usr/lib/mun/release", (shell / "src" / "systeminfo.cpp").read_text())
        postinst = (MKOSI / "mkosi.postinst.chroot").read_text()
        for key in ("version = \"$IMAGE_VERSION\"", "environment = \"$MUN_ENVIRONMENT\"", "release = $MUN_RELEASE"):
            self.assertIn(key, postinst, "the release file the shell reads comes from the build's own metadata")


class InputTests(unittest.TestCase):
    def setUp(self):
        self.inputs = json.loads((OS / "inputs.json").read_text())

    def test_versions_are_concrete_development_versions(self):
        self.assertRegex(self.inputs["version"], r"^\d+\.\d+\.\d+-dev$")

    def test_every_archive_is_the_one_dated_snapshot(self):
        snapshot = self.inputs["debian"]["snapshot"]
        self.assertRegex(snapshot, r"^\d{8}T\d{6}Z$")
        for archive in self.inputs["debian"]["archives"]:
            self.assertTrue(archive["url"].startswith("https://snapshot.debian.org/archive/"), archive["url"])
            self.assertTrue(archive["url"].endswith("/" + snapshot), archive["url"])

    def test_the_builder_image_is_one_pinned_debian_build(self):
        builder = self.inputs["builder"]
        self.assertRegex(builder["sha512"], r"^[0-9a-f]{128}$")
        self.assertTrue(builder["url"].endswith("/" + builder["image"]), builder["url"])
        build = builder["url"].rsplit("/", 2)[-2]
        self.assertIn(build, builder["image"])
        self.assertEqual(builder["sums_url"], builder["url"].rsplit("/", 1)[0] + "/SHA512SUMS")

    def test_only_the_development_image_takes_the_hosts_time_zone(self):
        dev = MKOSI / "mkosi.profiles" / "qemu-dev"
        self.assertIn("ExtraTrees=extra", (dev / "mkosi.conf").read_text())
        unit = (dev / "extra/usr/lib/systemd/system/mun-lab-timezone.service").read_text()
        self.assertIn("Before=mun-shell.service", unit)
        script = (dev / "extra/usr/lib/mun/lab-timezone").read_text()
        self.assertIn("/usr/share/zoneinfo/$zone", script, "the zone must name an existing zone file")
        self.assertFalse(list((MKOSI / "mkosi.extra").rglob("mun-lab-timezone*")), "not in the common image")

    def test_the_image_carries_the_licences(self):
        build = (MKOSI / "mkosi.build.chroot").read_text()
        self.assertIn('"$MUN/LICENSE" "$DESTDIR/usr/share/doc/mun-os/LICENSE"', build)
        self.assertIn('"$MUN/NOTICE" "$DESTDIR/usr/share/doc/mun-os/NOTICE"', build)
        self.assertIn('"$MUN/NAME-AND-LOGO.txt" "$DESTDIR/usr/share/doc/mun-os/NAME-AND-LOGO.txt"', build,
                      "the logo is not under the licence: its own terms go with the image")
        notice = (ROOT / "NOTICE").read_text()
        self.assertIn("Copyright 2026 Iván Moreno Mendoza", notice)
        self.assertIn("NAME-AND-LOGO.txt", notice, "the notice points to the terms it does not cover")
        self.assertIn("Logo.js", (ROOT / "NAME-AND-LOGO.txt").read_text())
        stage = (ROOT / "services" / "mun-shell" / "deploy" / "stage.sh").read_text()
        self.assertIn('fonts/*-OFL.txt', stage, "the typefaces compiled into the shell go with their licence")
        for font in (ROOT / "services" / "mun-shell" / "fonts").glob("*.ttf"):
            self.assertTrue((font.parent / f"{font.stem.split('-')[0]}-OFL.txt").is_file(), font.name)

    def test_every_input_states_its_distribution_conditions(self):
        for entry in (self.inputs["debian"], self.inputs["builder"]):
            self.assertTrue(entry.get("license"))

    def test_the_repository_carries_no_third_party_game(self):
        # MUN OS is agnostic of any game (owner, 2026-09-29): a port of a game
        # someone owns is built from a recipe kept outside (os/README.md).
        self.assertNotIn("sources", self.inputs, "no third-party game source is pinned here")
        self.assertEqual(sorted(p.name for p in (ROOT / "examples").iterdir() if p.is_dir()),
                         ["mun-collect", "mun-gl-probe"])
        build = (OS / "mkosi" / "mkosi.build.chroot").read_text()
        self.assertIn('"$SRCDIR"/recipes/*/', build, "other games come from recipes only")

    def test_sources_name_only_the_snapshot_and_check_signatures(self):
        text = inputs_tool.sources(self.inputs)
        self.assertEqual(text.count("Types: deb\n"), len(self.inputs["debian"]["archives"]))
        self.assertNotIn("deb.debian.org", text)
        self.assertNotIn("security.debian.org", text)
        self.assertNotIn("Trusted: yes", text)
        self.assertEqual(text.count("Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg"), 2)
        self.assertIn("Suites: trixie trixie-updates", text)
        self.assertIn("Suites: trixie-security", text)

    def test_get_prints_lists_one_per_line(self):
        self.assertEqual(inputs_tool.get(self.inputs, "builder.packages").splitlines(), self.inputs["builder"]["packages"])
        self.assertEqual(inputs_tool.get(self.inputs, "debian.snapshot"), self.inputs["debian"]["snapshot"])


class BuildInfoTests(unittest.TestCase):
    def test_build_info_names_identity_inputs_and_absences(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "games" / "mun-collect").mkdir(parents=True)
            game = tmp / "games" / "mun-collect" / "mun-collect"
            game.write_bytes(b"\x7fELF game")
            game.chmod(0o755)
            image = tmp / "mun-os-0.1.0-dev-qemu-arm64.qcow2"
            image.write_bytes(b"qcow2")
            raw = tmp / "mun-os.raw"
            raw.write_bytes(b"raw")
            manifest = tmp / "manifest.json"
            manifest.write_text(json.dumps({"packages": [
                {"type": "deb", "name": "systemd", "version": "257.13-1~deb13u1", "architecture": "arm64"},
                {"type": "deb", "name": "linux-image-6.12.107+deb13-arm64", "version": "6.12.107-1", "architecture": "arm64"},
                {"type": "deb", "name": "libgl1-mesa-dri", "version": "25.0.7-2+deb13u1", "architecture": "arm64"}]}))
            builder = tmp / "builder-packages.tsv"
            builder.write_text("mkosi\t25.3-7\tall\nsystemd\t257.13-1~deb13u1\tarm64\n")
            toolchain = tmp / "toolchain.tsv"
            toolchain.write_text("gcc\t4:14.2.0-1\tarm64\ncmake\t3.31.6-2\tarm64\n")
            initrd_manifest = tmp / "initrd-manifest.json"
            initrd_manifest.write_text(json.dumps({"packages": [
                {"type": "deb", "name": "lvm2", "version": "2.03.31-2", "architecture": "arm64"}]}))
            initrd = tmp / "initrd.cpio.zst"
            initrd.write_bytes(b"cpio")
            (tmp / "games" / "mygame").mkdir()
            ported = tmp / "games" / "mygame" / "mygame"
            ported.write_bytes(b"\x7fELF ported")
            ported.chmod(0o755)
            recipe = {"name": "mygame", "license": "GPL-2.0", "recipe_sha256": "d" * 64,
                      "sources": [{"path": "source", "git": "https://example.invalid/mygame", "commit": "e" * 40}],
                      "patches": []}
            (tmp / "recipes.json").write_text(json.dumps([recipe]))
            source = tmp / "source.json"
            source.write_text(json.dumps({"commit": "c" * 40, "clean": True}))
            out = tmp / "info.json"
            with out.open("w") as handle:
                subprocess.run([sys.executable, str(OS / "builder" / "build_info.py"),
                                "--inputs", str(OS / "inputs.json"), "--source-info", str(source), "--profile", "qemu-dev",
                                "--build-id", "20260927T000000Z-cccccccccccc", "--started", "2026-09-27T00:00:00Z",
                                "--image", str(image), "--raw", str(raw), "--manifest", str(manifest),
                                "--initrd-manifest", str(initrd_manifest), "--initrd", str(initrd),
                                "--builder-packages", str(builder), "--toolchain", str(toolchain),
                                "--config", str(MKOSI), "--out", str(tmp)],
                               check=True, stdout=handle)
            info = json.loads(out.read_text())
        self.assertEqual((info["name"], info["environment"], info["release"]), ("MUN OS", "qemu-arm64", False))
        self.assertTrue(info["version"].endswith("-dev"))
        self.assertEqual(info["kernel"]["packages"], {"linux-image-6.12.107+deb13-arm64": "6.12.107-1"})
        self.assertEqual(info["mesa"], {"libgl1-mesa-dri": "25.0.7-2+deb13u1"})
        self.assertTrue(info["firmware"].startswith("not installed"), "an absent component says so")
        self.assertEqual(info["toolchain"], {"gcc": "4:14.2.0-1", "cmake": "3.31.6-2"})
        self.assertEqual(info["builder"]["tools"]["mkosi"], "25.3-7")
        self.assertEqual([p["name"] for p in info["packages"]], sorted(p["name"] for p in info["packages"]))
        self.assertEqual(info["games"]["mun-collect"]["sha256"], hashlib.sha256(b"\x7fELF game").hexdigest())
        self.assertNotIn("recipe", info["games"]["mun-collect"])
        self.assertEqual(info["games"]["mygame"]["recipe"], recipe, "a recipe's game records its recipe")
        self.assertIn("mun-os-0.1.0-dev-qemu-arm64.qcow2", info["artifacts"])
        self.assertIn("mkosi.conf", info["config"]["files"])
        self.assertEqual(info["initrd"]["packages"], [{"name": "lvm2", "version": "2.03.31-2", "architecture": "arm64"}])
        self.assertEqual(info["initrd"]["sha256"], hashlib.sha256(b"cpio").hexdigest())
        self.assertEqual(info["debian"]["snapshot"], json.loads((OS / "inputs.json").read_text())["debian"]["snapshot"])


class EntryPointTests(unittest.TestCase):
    def run_mun(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "mun"), *args], capture_output=True, text=True)

    def test_an_official_build_is_refused_until_hardware_is_selected(self):
        result = self.run_mun("build")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not selected", result.stderr)
        self.assertIn("./mun dev build", result.stderr)

    def test_help_lists_the_development_route(self):
        result = self.run_mun()
        self.assertEqual(result.returncode, 0)
        for command in ("./mun dev build", "./mun dev run", "./mun dev vm", "./mun card", "./mun vm"):
            self.assertIn(command, result.stdout)

    def test_the_card_and_lab_tools_are_reached_through_mun(self):
        cards = self.run_mun("card", "variants")
        self.assertEqual(cards.returncode, 0, cards.stderr)
        self.assertIn("bad-arch", cards.stdout)
        lab = self.run_mun("vm", "--help")
        self.assertEqual(lab.returncode, 0)
        self.assertIn("./mun vm", lab.stdout)


if __name__ == "__main__":
    unittest.main()
