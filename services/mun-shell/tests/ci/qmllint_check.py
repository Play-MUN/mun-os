#!/usr/bin/env python3
"""qmllint's warnings on MUN Shell, against the ones already known.

    qmllint_check.py LOG BASELINE            # fail on a warning not in BASELINE
    qmllint_check.py LOG BASELINE --write    # record LOG's warnings as BASELINE

LOG is the output of the build's `all_qmllint` target. A warning is kept as
its file, its message and its category, without the line and column, so that
editing a file does not turn its known warnings into new ones; a file that
gains a second warning of the same kind gains a line. Warnings that went away
are listed, so the baseline can shrink; only new ones fail.
"""

import re
import sys
from collections import Counter
from pathlib import Path

WARNING = re.compile(r"^Warning: .*?(qml/[^:]+):\d+:\d+: (.*) \[([a-z-]+)\]$")


def warnings(text):
    found = Counter()
    for line in text.splitlines():
        match = WARNING.match(line)
        if match:
            found["\t".join(match.groups())] += 1
    return found


def main(argv):
    if len(argv) not in (2, 3) or (len(argv) == 3 and argv[2] != "--write"):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    log, baseline = Path(argv[0]), Path(argv[1])
    current = warnings(log.read_text(encoding="utf-8", errors="replace"))
    if len(argv) == 3:
        baseline.write_text("".join(f"{key}\n" * count for key, count in sorted(current.items())), encoding="utf-8")
        print(f"{sum(current.values())} warnings recorded in {baseline}")
        return 0
    known = Counter(line for line in baseline.read_text(encoding="utf-8").splitlines() if line)
    new, gone = current - known, known - current
    for key in sorted(gone):
        print("no longer reported (the baseline can drop it):", key.replace("\t", " | "))
    for key in sorted(new):
        print("new qmllint warning:", key.replace("\t", " | "), file=sys.stderr)
    print(f"qmllint: {sum(current.values())} warnings, {sum(new.values())} new, {sum(gone.values())} gone")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
