"""mun-cardd as it runs: the real `main()`, its sockets, its queue and its
loop, with only the platform replaced (no udev, no mounts, no root).

What is checked here is that the loop outlives its work: a line that is not
a request, a request whose handling fails, a worker thread or a scheduled
callback that raises are each that one piece's failure, answered and logged,
and the service goes on serving the card and the session it has."""

import json
import socket
import time
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

import cardd  # noqa: E402
from test_cardd import RealDirMounter, game_info  # noqa: E402


class BrokenReaderMounter(RealDirMounter):
    """Mounts as RealDirMounter does, except a device named `vdx`, for which
    the platform fails in a way the service does not expect."""

    def mount(self, card, path):
        if card.device == "vdx":
            raise RuntimeError("the platform failed in an unforeseen way")
        return super().mount(card, path)


class RunningService:
    """`cardd.main()` on a thread, with temporary sockets. `devices` is what
    a rescan finds; `udev` delivers an event as the monitor would."""

    def __init__(self, test, devices=(("vdc", "NPT-game", "/dev/vdc"),)):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardd-"))
        test.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.control = self.tmp / "control.sock"
        self.devices = list(devices)
        self.logs, self.handlers, self.udev = [], {}, None
        self.mounter = BrokenReaderMounter(self.tmp / "cards")
        (self.tmp / "launch").mkdir()

        def monitor(callback):
            self.udev = callback
        for p in (patch.object(cardd, "CONTROL_SOCKET_PATH", self.control),
                  patch.object(cardd, "SHAPE_ROOT", self.tmp / "shape"),
                  patch.object(cardd, "LAUNCH_ROOT_PREFIX", str(self.tmp / "launch") + "/"),
                  patch.object(cardd, "Mounter", lambda root: self.mounter),
                  patch.object(cardd, "validate_mount", lambda mount: game_info()),
                  patch.object(cardd, "enumerate_devices", lambda: list(self.devices)),
                  patch.object(cardd, "start_udev_monitor", monitor),
                  patch.object(cardd, "log", self.logs.append),
                  # Signals belong to the main thread; the loop is stopped through its own handler.
                  patch.object(cardd.signal, "signal", lambda number, handler: self.handlers.__setitem__(number, handler))):
            p.start(); test.addCleanup(p.stop)
        self.result = []
        self.thread = threading.Thread(target=self.run, name="cardd-main", daemon=True)
        self.thread.start()
        test.addCleanup(self.stop)
        waited = threading.Event()
        for _ in range(500):
            if self.control.exists() and (not self.devices or self.card()):
                return
            if not self.thread.is_alive():
                break
            waited.wait(0.01)
        raise AssertionError(f"the service did not start: {self.result} {self.logs}")

    def run(self):
        try:
            self.result.append(cardd.main(["--socket", str(self.tmp / "cardd.sock"),
                                           "--mount-root", str(self.tmp / "mounts"), "--rescan", "0.2"]))
        except BaseException as exc:      # noqa: BLE001 - the defect under test: the loop ended
            self.result.append(exc)

    def stop(self):
        if self.thread.is_alive() and cardd.signal.SIGTERM in self.handlers:
            self.handlers[cardd.signal.SIGTERM](cardd.signal.SIGTERM, None)
        self.thread.join(5)

    def card(self):
        """The valid card, as a new client of the event socket is told."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(self.tmp / "cardd.sock"))
            data = b""
            while not data.endswith(b"\n"):
                data += sock.recv(1 << 16)
        cards = [c for c in json.loads(data.decode("utf-8"))["cards"] if c["state"] == "valid"]
        return cards[0] if cards else None

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        deadline = time.monotonic() + 5
        while True:
            try:
                sock.connect(str(self.control))
                return sock
            except (ConnectionRefusedError, BlockingIOError):
                # The control socket's queue is short and full for a moment:
                # refused on macOS, "try again" on Linux.
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.01)

    @staticmethod
    def exchange(sock, line):
        """Send one line (bytes, or a request to serialise as the launcher does) and read one reply."""
        sock.sendall((line if isinstance(line, bytes) else json.dumps(line).encode("utf-8")) + b"\n")
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(1 << 16)
            if not chunk:
                raise AssertionError(f"the service closed the connection without answering {line[:60]!r}")
            data += chunk
        return json.loads(data.decode("utf-8"))            # strict UTF-8, one line

    def ask(self, line):
        with self.connect() as sock:
            return self.exchange(sock, line)

    def save_request(self, **over):
        card = self.card()
        request = {"type": "save", "slot": card["slot"], "insertion": card["insertion"], "serial": card["serial"],
                   "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1, "payload": {"x": 1}}
        request.update(over)
        return request

    def tracebacks(self):
        return [entry for entry in self.logs if "Traceback (most recent call last)" in entry]

    def assert_serving(self, test):
        """Still the same service: the loop runs, the card is the same insertion, a save is written."""
        test.assertTrue(self.thread.is_alive(), f"the service ended: {self.result}")
        reply = self.ask(self.save_request())
        test.assertTrue(reply.get("ok"), reply)


class ServiceLoopTests(unittest.TestCase):
    def test_an_ordinary_session_saves_and_releases_through_the_real_loop(self):
        service = RunningService(self)
        insertion = service.card()["insertion"]
        reply = service.ask(service.save_request(payload={"level": 3, "name": "ñ"}))
        self.assertEqual((reply["type"], reply["ok"], reply["session"]), ("saved", True, "s1"))
        saved = json.loads((service.tmp / "cards" / "c1" / "saves" / "mun.collect" / "save.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["payload"], {"level": 3, "name": "ñ"})
        self.assertEqual(service.card()["insertion"], insertion)
        self.assertTrue(service.ask({"type": "release", "serial": "NPT-game"})["ok"])
        self.assertEqual(service.tracebacks(), [])
        service.stop()
        self.assertEqual(service.result, [0])

    def test_lines_that_are_not_a_request_are_answered_and_the_service_goes_on(self):
        service = RunningService(self)
        insertion = service.card()["insertion"]
        lines = [b"1", b"[]", b'"save"', b"null", b"true", b"3.5", b"{", b"\xff\xfe\x00", b"",
                 b"[" * 60000,                      # deeper than the JSON parser goes
                 b'{"type": 5}', b'{"type": ["stage"]}', b'{"type": {"save": 1}}']
        with service.connect() as sock:              # one connection: each line gets its answer
            for line in lines:
                reply = service.exchange(sock, line)
                self.assertEqual((reply["type"], reply["error"]["code"]), ("error", "bad_request"), line[:40])
            self.assertEqual(service.exchange(sock, {"type": "release", "serial": "NPT-none"}),
                             {"type": "released", "ok": True, "detail": "no such card"})
        for line in lines:                           # and as the only line of a connection
            self.assertEqual(service.ask(line)["error"]["code"], "bad_request", line[:40])
        service.assert_serving(self)
        self.assertEqual(service.card()["insertion"], insertion, "not restarted: the session's card is the same insertion")
        self.assertEqual(service.tracebacks(), [], "a line that is not a request is not an internal error")

    def test_a_save_with_text_utf8_cannot_carry_is_refused_on_the_wire(self):
        service = RunningService(self)
        request = service.save_request(payload={"n": "\ud800"}, session="s\ud800")
        wire = json.dumps(request).encode("ascii")             # as the launcher forwards it: the escape, in ASCII
        self.assertIn(b'"\\ud800"', wire)
        reply = service.ask(wire)
        self.assertEqual((reply["type"], reply["ok"], reply["error"]["code"]), ("saved", False, "save_invalid"))
        self.assertEqual(reply["session"], "s?")
        service.assert_serving(self)
        self.assertEqual(service.tracebacks(), [])

    def test_a_request_whose_handling_fails_is_answered_with_an_error(self):
        service = RunningService(self)
        for kind, answer in (("release", "released"), ("save", "saved"), ("stage", "staged")):
            with self.subTest(request=kind), service.connect() as sock, \
                    patch.object(cardd.CardManager, kind, side_effect=RuntimeError("an unforeseen failure")):
                before = len(service.tracebacks())
                reply = service.exchange(sock, {"type": kind, "serial": "NPT-game", "session": "s7"})
                self.assertEqual((reply["type"], reply["ok"], reply["error"]["code"]), (answer, False, "internal_error"))
                self.assertEqual(reply.get("session"), "s7")
                logged = service.tracebacks()[before:]
                self.assertEqual(len(logged), 1, "logged once")
                self.assertIn("RuntimeError: an unforeseen failure", logged[0])
                # The same connection is still served.
                self.assertEqual(service.exchange(sock, b"[]")["error"]["code"], "bad_request")
        service.assert_serving(self)

    def test_a_save_worker_that_fails_unexpectedly_answers_and_frees_the_card(self):
        service = RunningService(self)
        with patch.object(cardd, "_write_save", side_effect=RuntimeError("an unforeseen failure")):
            reply = service.ask(service.save_request())
        self.assertEqual((reply["type"], reply["ok"], reply["error"]["code"]), ("saved", False, "write_failed"))
        self.assertEqual(reply["session"], "s1")
        logged = service.tracebacks()
        self.assertEqual(len(logged), 1); self.assertIn("RuntimeError: an unforeseen failure", logged[0])
        # Not left "saving": the next save is written, and a safe release is not kept waiting.
        service.assert_serving(self)
        self.assertTrue(service.ask({"type": "release", "serial": "NPT-game"})["ok"])

    def test_a_stage_worker_that_fails_unexpectedly_answers(self):
        service = RunningService(self)
        card = service.card()
        dest = service.tmp / "launch" / "s1"; (dest / "work").mkdir(parents=True)
        request = {"type": "stage", "slot": card["slot"], "insertion": card["insertion"], "serial": card["serial"],
                   "version": "0.1.0", "session": "s1", "dest": str(dest)}
        with patch.object(cardd, "_copy_save_out", side_effect=RuntimeError("an unforeseen failure")):
            reply = service.ask(request)
        self.assertEqual((reply["type"], reply["ok"], reply["error"]["code"]), ("staged", False, "stage_failed"))
        self.assertEqual(reply["session"], "s1")
        self.assertEqual(len(service.tracebacks()), 1)
        (dest / "game").unlink()
        reply = service.ask(request)                 # the valid case: the same request, staged
        self.assertTrue(reply["ok"], reply); self.assertEqual(reply["save"], {"present": False})
        service.assert_serving(self)

    def test_a_scheduled_callback_that_raises_does_not_end_the_loop(self):
        service = RunningService(self, devices=())
        service.udev("add", "vdx", "NPT-broken", "/dev/vdx")        # its mount fails in an unforeseen way
        service.udev("add", "vdc", "NPT-game", "/dev/vdc")          # scheduled behind it
        waited = threading.Event()
        for _ in range(500):
            if service.tracebacks():
                break
            waited.wait(0.01)
        logged = service.tracebacks()
        self.assertEqual(len(logged), 1, "logged once")
        self.assertIn("RuntimeError: the platform failed in an unforeseen way", logged[0])
        self.assertTrue(service.thread.is_alive(), f"the service ended: {service.result}")
        # The loop still answers, timers and rescans still run, and the service stops when told.
        self.assertEqual(service.ask({"type": "release", "serial": "NPT-none"})["ok"], True)
        waited.wait(0.5)
        self.assertEqual(len(service.tracebacks()), 1, "a rescan does not meet the same failure again")
        service.stop()
        self.assertEqual(service.result, [0])

    def test_a_rescan_that_raises_does_not_end_the_loop(self):
        service = RunningService(self)
        boom = patch.object(cardd, "enumerate_devices", side_effect=RuntimeError("sysfs failed in an unforeseen way"))
        boom.start()
        try:
            waited = threading.Event()
            for _ in range(500):
                if service.tracebacks():
                    break
                waited.wait(0.01)
            self.assertIn("RuntimeError: sysfs failed in an unforeseen way", service.tracebacks()[0])
        finally:
            boom.stop()
        service.assert_serving(self)


if __name__ == "__main__":
    unittest.main()
