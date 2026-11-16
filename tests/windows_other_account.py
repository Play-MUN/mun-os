"""Run by the Hosts workflow on Windows as another local account: tries to
connect to the running guest's QMP socket, whose path is given.

Only a refusal for want of rights passes: Winsock's WSAEACCES (EACCES
elsewhere). Nothing listening, a path that does not exist or any other
error says nothing about the account's rights and does not pass; nor does
it pass while the socket does not answer its owner, which the workflow
checks just before and just after this, with the same path.

Exit 0: access denied, as it must be. 1: connected. 2: refused for another
reason, which proves nothing. 3: the check itself could not run."""
import errno
import sys
from pathlib import Path

WSAEACCES = 10013
DENIED = (WSAEACCES, errno.EACCES)


def verdict(attempt) -> "tuple[int, str]":
    """The exit status and what to say for one attempt to connect
    (`attempt` connects and closes)."""
    try:
        attempt()
    except OSError as refused:
        if isinstance(refused, PermissionError) and refused.errno in DENIED:
            return 0, f"access denied, as it must be (error {refused.errno})"
        return 2, (f"refused, but not for want of rights ({type(refused).__name__} {refused.errno}: {refused}); "
                   "that proves nothing")
    return 1, "CONNECTED from another account"


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: windows_other_account.py QMP-SOCKET")
        return 3
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vm"))
        import host
        status, message = verdict(lambda: host.connect(Path(argv[1]), 5.0).close())
    except Exception as broken:  # noqa: BLE001 - the check did not run, which is not a refusal
        print(f"the check could not run: {broken!r}")
        return 3
    print(message)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
