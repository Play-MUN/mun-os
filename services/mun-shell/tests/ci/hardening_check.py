#!/usr/bin/env python3
"""The hardening a built MUN Shell carries, read from what was built.

    hardening_check.py BINARY [--build-dir DIR]

Two kinds of evidence, kept apart in the output, so that a flag that stops
reaching the binary, or a toolchain that ignores it, fails here instead of
going unnoticed.

In the executable, read with readelf (binutils):

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

In the build's own commands (--build-dir, the CMake build directory:
compile_commands.json and build.ninja), for what an executable cannot show:
every object compiled with -fstack-protector-strong (the executable only
shows that some function has a canary), -fstack-clash-protection (it leaves
no mark at all), -D_FORTIFY_SOURCE=3 (only calls that could be fortified
show), and the architecture's control-flow flag; and mun-shell linked with
-z relro, -z now and -z noexecstack.

Prints one line per property and exits 1 if a required one is missing.
"""

import json
import re
import shlex
import shutil
import subprocess  # nosec B404: runs readelf, a fixed program, on the build's own files
import sys


def readelf(*args):
    return subprocess.run(["readelf", "-W", *args], check=True, capture_output=True, text=True).stdout  # nosec B603


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


def configuration(build_dir, machine):
    """What the build directory says each object was compiled and the
    executable linked with."""
    try:
        commands = json.loads(open(f"{build_dir}/compile_commands.json", encoding="utf-8").read())
        ninja = open(f"{build_dir}/build.ninja", encoding="utf-8").read()
    except (OSError, ValueError) as error:
        return [(f"the build's commands ({error})", False)]
    flags = ["-fstack-protector-strong", "-fstack-clash-protection", "-D_FORTIFY_SOURCE=3"]
    flags += {"AArch64": ["-mbranch-protection=standard"], "X86-64": ["-fcf-protection=full"]}.get(
        next((arch for arch in ("AArch64", "X86-64") if arch in machine), ""), [])
    found = []
    for flag in flags:
        lacking = [entry["file"] for entry in commands
                   if flag not in (entry["arguments"] if "arguments" in entry else shlex.split(entry["command"]))]
        where = f"; not in {', '.join(lacking[:3])}{', …' if len(lacking) > 3 else ''}" if lacking else ""
        found.append((f"{flag} in every compile command ({len(commands) - len(lacking)} of {len(commands)}{where})",
                      bool(commands) and not lacking))
    edge = re.search(r"^build mun-shell: .*\n((?:[ \t]+.*\n)*)", ninja, re.M)
    link = re.search(r"^\s*LINK_FLAGS = (.*)$", edge.group(1), re.M) if edge else None
    linked = link.group(1).split() if link else []
    for flag in ("-Wl,-z,relro", "-Wl,-z,now", "-Wl,-z,noexecstack"):
        found.append((f"{flag} when mun-shell is linked", flag in linked))
    return found


def report(title, found):
    print(title)
    for name, ok in found:
        print(f"  {'info   ' if ok is None else 'ok     ' if ok else 'MISSING'} {name}")
    return all(ok is not False for _, ok in found)


def main(argv):
    build_dir = None
    if len(argv) == 3 and argv[1] == "--build-dir":
        build_dir = argv[2]
    elif len(argv) != 1:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    if not shutil.which("readelf"):
        print("hardening: readelf (binutils) not found", file=sys.stderr)
        return 2
    machine, found = properties(argv[0])
    ok = report(f"hardening of {argv[0]} ({machine}), in the executable:", found)
    if build_dir is not None:
        ok = report(f"hardening of {argv[0]}, in the build's commands ({build_dir}):",
                    configuration(build_dir, machine)) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
