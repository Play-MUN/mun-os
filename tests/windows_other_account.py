"""Run by the Hosts workflow on Windows as another local account: tries to
connect to the running guest's QMP socket, whose path is given. Exit 0 if
the connection is refused (as it must be), 1 if it connects, 3 if the check
itself could not run. A refusal proves something only while the socket
answers its owner: the workflow connects as the owner just before and just
after this, with the same path."""
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vm"))
    import host
except Exception as broken:  # noqa: BLE001 - the check did not run, which is not a refusal
    print(f"the check could not run: {broken!r}")
    sys.exit(3)

try:
    host.connect(Path(sys.argv[1]), 5.0).close()
except OSError as refused:
    print(f"refused, as it must be: {type(refused).__name__} {refused.errno}: {refused}")
    sys.exit(0)
print("CONNECTED from another account")
sys.exit(1)
