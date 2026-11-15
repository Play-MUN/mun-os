"""The laboratory's control channel on a real Windows host (run there by the
Hosts workflow; skipped elsewhere): QMP and qemu-ga are AF_UNIX sockets in
the user's own %LOCALAPPDATA%, reached through Winsock (vm/host.py), by an
ASCII path even from a profile folder with accents and spaces (vm/munvm.py)."""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm"))
import host  # noqa: E402

SPEC = importlib.util.spec_from_file_location("munvm_windows", ROOT / "vm" / "munvm.py")
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)

WINDOWS = sys.platform == "win32"


def listen_unix(path: Path):
    """A listening AF_UNIX socket at `path` made through Winsock, as QEMU
    makes its own; returns the listening socket object."""
    import ctypes
    ws2 = ctypes.WinDLL("ws2_32", use_last_error=True)
    ws2.socket.restype = ctypes.c_size_t
    ws2.socket.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int)
    ws2.bind.argtypes = (ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int)
    ws2.listen.argtypes = (ctypes.c_size_t, ctypes.c_int)

    class SockaddrUn(ctypes.Structure):
        _fields_ = [("sun_family", ctypes.c_ushort), ("sun_path", ctypes.c_char * 108)]

    handle = ws2.socket(1, 1, 0)
    address = SockaddrUn(1, str(path).encode("utf-8"))
    if ws2.bind(handle, ctypes.byref(address), ctypes.sizeof(address)) != 0 or ws2.listen(handle, 4) != 0:
        raise OSError(ctypes.get_last_error(), f"cannot listen on {path}")
    return socket.socket(fileno=handle)


@unittest.skipUnless(WINDOWS, "a Windows host")
class WindowsChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mun win "))

    def socket_path(self, local: Path) -> Path:
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
            return vm.socket_path(self.tmp / "guest root", "qmp.sock")

    def test_a_connection_reaches_a_listener_and_carries_bytes_both_ways(self):
        local = self.tmp / "Local"
        local.mkdir()
        path = self.socket_path(local)
        listener = listen_unix(path)
        self.addCleanup(listener.close)

        def answer():
            conn, _ = listener.accept()
            with conn:
                conn.sendall(b"greeting\n" + conn.recv(64))
        threading.Thread(target=answer, daemon=True).start()
        with host.connect(path, 5.0) as sock:
            sock.sendall(b"ping\n")
            received = b""
            while received.count(b"\n") < 2:
                chunk = sock.recv(64)
                if not chunk:
                    break
                received += chunk
        self.assertEqual(received, b"greeting\nping\n")

    def test_nothing_listening_is_a_refusal_not_a_hang(self):
        path = self.socket_path(self.tmp / "Local")
        with self.assertRaises(OSError):
            host.connect(path, 2.0)

    def test_a_profile_folder_with_accents_and_spaces_is_reached_by_an_ascii_path(self):
        local = self.tmp / "Iván Pérez" / "AppData" / "Local"
        local.mkdir(parents=True)
        try:
            path = self.socket_path(local)
        except vm.LabError as refused:
            # Allowed only where the volume keeps no short (8.3) names.
            out = subprocess.run(["fsutil", "8dot3name", "query", str(local)[:2]], capture_output=True, text=True)
            self.skipTest(f"no short names on this volume: {refused}; fsutil: {out.stdout.strip()[:200]}")
        self.assertTrue(str(path).isascii(), path)
        listener = listen_unix(path)
        self.addCleanup(listener.close)
        threading.Thread(target=lambda: listener.accept()[0].close(), daemon=True).start()
        host.connect(path, 5.0).close()

    def test_the_sockets_folder_is_closed_to_other_accounts(self):
        # The user's own %LOCALAPPDATA% is not readable by other users; no
        # entry of the sockets' folder may grant them access either.
        path = vm.socket_path(self.tmp / "guest root", "qmp.sock")
        listing = subprocess.run(["icacls", str(path.parent)], capture_output=True, text=True).stdout
        for broad in ("Everyone", "BUILTIN\\Users", "NT AUTHORITY\\Authenticated Users", "Todos", "Usuarios"):
            self.assertNotIn(broad + ":", listing, listing)


if __name__ == "__main__":
    unittest.main()
