#!/usr/bin/env python3
"""The shell job's hardening gate (services/mun-shell/tests/ci/hardening_check.py),
on readelf output taken from real MUN Shell builds (Debian 13, AArch64): the
build before its hardening flags and the build after."""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hardening_check", ROOT / "services/mun-shell/tests/ci/hardening_check.py")
hardening_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hardening_check)

HEADER = """  Type:                              DYN (Position-Independent Executable file)
  Machine:                           AArch64
"""
SEGMENTS = """  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
  GNU_RELRO      0x8e4100 0x00000000008e4100 0x00000000008e4100 0x00bf00 0x00bf00 R   0x1
"""
HARDENED = {
    "-h": HEADER, "-l": SEGMENTS,
    "-d": """ 0x000000000000001e (FLAGS)              BIND_NOW
 0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE
""",
    "--dyn-syms": """    84: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND memcpy@GLIBC_2.17 (4)
   180: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __memcpy_chk@GLIBC_2.17 (4)
   870: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND __stack_chk_guard@GLIBC_2.17 (30)
   882: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __stack_chk_fail@GLIBC_2.17 (4)
""",
    "-n": """  GNU                  0x00000010	NT_GNU_PROPERTY_TYPE_0	      Properties: AArch64 feature: BTI, PAC
""",
}
UNHARDENED = {
    "-h": HEADER,
    "-l": SEGMENTS.replace("0x8e4100 0x00000000008e4100 0x00000000008e4100 0x00bf00 0x00bf00", "0x8b5b50 0x00000000008c5b50 0x00000000008c5b50 0x00a4b0 0x00a4b0"),
    "-d": " 0x000000006ffffffb (FLAGS_1)            Flags: PIE\n",
    "--dyn-syms": "    84: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND memcpy@GLIBC_2.17 (4)\n",
    "-n": "  GNU                  0x00000010\tNT_GNU_ABI_TAG (ABI version tag)\n",
}


class HardeningCheckTests(unittest.TestCase):
    def run_check(self, outputs, which="/usr/bin/readelf"):
        out = io.StringIO()
        with patch.object(hardening_check, "readelf", lambda flag, binary: outputs[flag]), \
                patch.object(hardening_check.shutil, "which", lambda name: which), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = hardening_check.main(["mun-shell"])
        return code, out.getvalue()

    def test_the_hardened_build_passes_every_property(self):
        code, said = self.run_check(HARDENED)
        self.assertEqual(code, 0, said)
        self.assertNotIn("MISSING", said)
        self.assertIn("fortified calls (1: __memcpy_chk)", said)

    def test_the_build_before_its_flags_misses_four(self):
        code, said = self.run_check(UNHARDENED)
        self.assertEqual(code, 1)
        missing = [line for line in said.splitlines() if "MISSING" in line]
        self.assertEqual(len(missing), 4, said)
        for name in ("full RELRO", "stack protector", "fortified calls", "control-flow protection"):
            self.assertTrue(any(name in line for line in missing), name)

    def test_bind_now_is_read_from_either_flags_entry(self):
        for dynamic in (" 0x000000000000001e (FLAGS)              BIND_NOW\n",
                        " 0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE\n"):
            with self.subTest(dynamic=dynamic.strip()):
                outputs = dict(HARDENED, **{"-d": dynamic + " 0x000000006ffffffb (FLAGS_1)            Flags: PIE\n"})
                self.assertNotIn("MISSING full RELRO", self.run_check(outputs)[1])

    def test_an_executable_stack_fails(self):
        outputs = dict(HARDENED, **{"-l": SEGMENTS.replace(" RW  0x10", " RWE 0x10")})
        code, said = self.run_check(outputs)
        self.assertEqual(code, 1)
        self.assertIn("MISSING non-executable stack", said)

    def test_only_one_of_bti_and_pac_fails(self):
        outputs = dict(HARDENED, **{"-n": HARDENED["-n"].replace("BTI, PAC", "PAC")})
        self.assertEqual(self.run_check(outputs)[0], 1)

    def test_x86_64_reports_cet_without_requiring_it_but_requires_the_rest(self):
        x86 = dict(HARDENED, **{"-h": HEADER.replace("AArch64", "Advanced Micro Devices X86-64"),
                                "-n": "  GNU                  0x00000010\tNT_GNU_PROPERTY_TYPE_0\t      Properties: x86 ISA needed: x86-64-baseline\n"})
        code, said = self.run_check(x86)
        self.assertEqual(code, 0, said)
        self.assertIn("info    control-flow protection (IBT and SHSTK; note: none); not required on x86-64", said)
        self.assertEqual(self.run_check(dict(x86, **{"--dyn-syms": UNHARDENED["--dyn-syms"]}))[0], 1)

    def test_aarch64_without_its_marking_fails(self):
        code, said = self.run_check(dict(HARDENED, **{"-n": UNHARDENED["-n"]}))
        self.assertEqual(code, 1)
        self.assertIn("MISSING control-flow protection (BTI and PAC; note: none)", said)

    def test_without_readelf_it_is_a_usage_error(self):
        self.assertEqual(self.run_check(HARDENED, which=None)[0], 2)


if __name__ == "__main__":
    unittest.main()
