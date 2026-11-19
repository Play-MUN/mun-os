#!/usr/bin/env python3
"""The hardening a built MUN Shell carries, read from the executable itself.

    hardening_check.py BINARY

Reads the ELF file with readelf (binutils) and requires what the build asks
the compiler and linker for (CMakeLists.txt), so that a flag that stops
reaching the binary, or a toolchain that ignores it, fails here instead of
going unnoticed:

- position-independent executable (ELF type DYN, flagged PIE);
- full RELRO: a GNU_RELRO segment and immediate binding (BIND_NOW);
- a stack that is not executable (GNU_STACK without E);
- the stack protector: the binary calls __stack_chk_fail;
- fortified calls: it calls at least one of glibc's __*_chk functions;
- control-flow protection marked in its GNU property note: BTI and PAC on
  AArch64, the console's architecture.

On x86-64, where the shell is built only for checks, the IBT and SHSTK
marking is reported but not required: the linker marks an executable only
when every object in it is marked, and Debian 13's x86-64 startup files
(crt1.o, crti.o, crtn.o) are not, so no x86-64 executable of that system can
be. The five other properties are required there too.

Prints one line per property and exits 1 if a required one is missing.
"""

import re
import shutil
import subprocess
import sys


def readelf(*args):
    return subprocess.run(["readelf", "-W", *args], check=True, capture_output=True, text=True).stdout


def properties(binary):
    header, segments, dynamic = readelf("-h", binary), readelf("-l", binary), readelf("-d", binary)
    symbols, notes = readelf("--dyn-syms", binary), readelf("-n", binary)
    machine = re.search(r"Machine:\s+(.*)", header).group(1).strip()
    # "UND name@VERSION (index)": readelf ends an imported symbol's line with
    # its version and that version's index.
    imported = set(re.findall(r"\bUND\s+([^\s@]+)(?:@\S+)?(?:\s+\(\d+\))?\s*$", symbols, re.M))
    fortified = sorted(name for name in imported if re.fullmatch(r"__\w+_chk", name) and name != "__stack_chk_fail")
    stack = re.search(r"GNU_STACK\s+(?:\S+\s+){5}(\S+)", segments)
    # "(FLAGS)  BIND_NOW" and "(FLAGS_1)  Flags: NOW PIE": both may say it.
    flags = " ".join(re.findall(r"\((?:FLAGS|FLAGS_1)\)\s+(?:Flags:\s*)?(.*)", dynamic))
    if "AArch64" in machine:
        wanted, note, required = ("BTI", "PAC"), re.search(r"AArch64 feature:\s*(.*)", notes), True
    elif "X86-64" in machine:
        wanted, note, required = ("IBT", "SHSTK"), re.search(r"x86 feature:\s*(.*)", notes), False
    else:
        wanted, note, required = (), None, True
    marked = note.group(1) if note else ""
    cfi = bool(wanted) and all(word in marked for word in wanted)
    return machine, [
        ("position-independent executable", "DYN" in re.search(r"Type:\s+(\S+)", header).group(1) and "PIE" in flags),
        ("full RELRO (GNU_RELRO, BIND_NOW)", "GNU_RELRO" in segments and ("BIND_NOW" in flags or re.search(r"\bNOW\b", flags) is not None)),
        ("non-executable stack", stack is not None and "E" not in stack.group(1)),
        ("stack protector (__stack_chk_fail)", "__stack_chk_fail" in imported),
        (f"fortified calls ({len(fortified)}: {', '.join(fortified[:4])}{', …' if len(fortified) > 4 else ''})", bool(fortified)),
        (f"control-flow protection ({' and '.join(wanted) or 'none for this machine'}; note: {marked or 'none'})"
         + ("" if required else "; not required on x86-64, see above"), cfi if required else None),
    ]


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    if not shutil.which("readelf"):
        print("hardening: readelf (binutils) not found", file=sys.stderr)
        return 2
    machine, found = properties(argv[0])
    print(f"hardening of {argv[0]} ({machine}):")
    for name, ok in found:
        print(f"  {'info   ' if ok is None else 'ok     ' if ok else 'MISSING'} {name}")
    return 0 if all(ok is not False for _, ok in found) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
