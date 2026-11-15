"""mun-launchd's own main(): the real start-up order, event loop and sockets on
a host, with systemd, the game user and the signal handlers stood in for.
What one step of the loop cannot handle must stay that step's failure."""

import json
import os
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mun-launchd"))
sys.path.insert(0, str(ROOT / "tests"))

import launchd  # noqa: E402
import test_directory_saves as directory_saves  # noqa: E402

GAME_UNIT = ("ActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0\nExecMainCode=0\n"
             "LoadState=loaded\nExecMainStartTimestampMonotonic=123\n")


class Done:
    def __init__(self, stdout=""):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


class RunningLauncher:
    """launchd.main() in a thread. `units` are the session ids systemd lists as
    running game units when the launcher starts."""

    def __init__(self, case: unittest.TestCase, units=(), **settings):
        self.case, self.units = case, list(units)
        self.settings = {"POLL_INTERVAL": 0.02, "READER_GRACE": 60.0, **settings}     # launchd's own constants
        self.folder = Path(tempfile.mkdtemp(prefix="ml"))
        self.root = self.folder / "lr"
        self.root.mkdir()
        self.socket = self.folder / "l.sock"
        self.lines, self.handlers, self.error, self.managers, self.commands = [], {}, None, [], []
        self.thread = threading.Thread(target=self._main, daemon=True)
        case.addCleanup(shutil.rmtree, self.folder, True)

    def systemctl(self, cmd, **_):
        self.commands.append(list(cmd))
        if cmd[0] == "findmnt":
            return Done("rw,nosuid,nodev")
        if cmd[:2] == ["systemctl", "list-units"]:
            return Done("".join(f"mun-game-{sid}.service loaded active running Game session {sid}\n"
                                for sid in self.units) if "mun-game-*" in cmd else "")
        if cmd[:2] == ["systemctl", "show"]:
            return Done(GAME_UNIT if cmd[2].startswith("mun-game-")
                        else "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\n")
        return Done()

    def session_left_running(self, sid: str, saves=None) -> None:
        """What a launcher that ended leaves of a single-object game: the
        session's directory and record, and the name of a socket nobody serves."""
        (self.root / sid / "work").mkdir(parents=True)
        (self.root / sid / "session.json").write_text(json.dumps(
            {"slot": "c1", "serial": "NPT-x", "insertion": "i1", "card_id": "mun.collect", "title": "MUN Collect",
             "version": "1.0", "profile": "linux-arm64-v0", "saves": saves, "naming": "mun", "started_at": 0.0,
             "dest": str(self.root / sid)}))
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(str(self.root / sid / "save.sock"))
        dead.close()
        self.units.append(sid)

    def _main(self) -> None:
        def runner_init(runner, root=self.root):
            runner.root, runner.uid, runner.gid = root, os.getuid(), os.getgid()

        def manager(*args, **kwargs):
            self.managers.append(real_manager(*args, **kwargs))
            return self.managers[-1]

        real_manager, patch = launchd.SessionManager, unittest.mock.patch.object
        with patch(launchd, "LAUNCH_ROOT", self.root), patch(launchd, "RESULT_FILE", self.root / "last-result.json"), \
                patch(launchd, "CARDD_SOCKET", self.folder / "no-cardd.sock"), \
                patch(launchd, "CARDD_CONTROL", self.folder / "no-control.sock"), \
                patch(launchd, "CONTENT_MOUNT", self.folder / "cm"), patch(launchd, "log", self.lines.append), \
                unittest.mock.patch.multiple(launchd, **self.settings), \
                patch(launchd.subprocess, "run", self.systemctl), patch(launchd.Runner, "__init__", runner_init), \
                patch(launchd.os, "chown", lambda *a, **k: None), \
                patch(launchd.signal, "signal", self.handlers.__setitem__), \
                patch(launchd, "SessionManager", manager):
            try:
                launchd.main(["--socket", str(self.socket), "serve"])
            except BaseException as exc:  # noqa: BLE001 - the test reports how the launcher ended
                self.error = exc

    def __enter__(self) -> "RunningLauncher":
        self.thread.start()
        self.wait(lambda: signal.SIGTERM in self.handlers and self.managers and self.managers[0].gate_open,
                  "the launcher never reached its event loop")
        return self

    def __exit__(self, *_) -> None:
        if signal.SIGTERM in self.handlers:
            self.handlers[signal.SIGTERM]()
        self.thread.join(5)

    @property
    def manager(self) -> "launchd.SessionManager":
        return self.managers[0]

    def wait(self, condition, why: str, seconds: float = 5.0) -> None:
        deadline = time.monotonic() + seconds
        while not condition():
            if self.error is not None or not self.thread.is_alive():
                self.case.fail(f"the launcher ended: {self.error!r}")
            if time.monotonic() > deadline:
                self.case.fail(why)
            time.sleep(0.01)

    def alive(self) -> None:
        """The launcher still serves: a new shell client gets its snapshot."""
        self.case.assertEqual(self.shell_request(None)["type"], "snapshot", f"launcher ended: {self.error!r}")

    def connect(self, path: Path) -> socket.socket:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.case.addCleanup(conn.close)
        conn.settimeout(5)
        deadline = time.monotonic() + 5
        while True:
            try:
                conn.connect(str(path))
                return conn
            except (ConnectionRefusedError, BlockingIOError):
                # The listener's queue is full for a moment: refused on
                # macOS, "try again" on Linux.
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.01)

    def shell_request(self, request) -> dict:
        """One shell client: its snapshot, or the answer to `request` after it."""
        conn = self.connect(self.socket)
        lines = _lines(conn)
        answer = next(lines)
        if request is not None:
            conn.sendall(json.dumps(request).encode() + b"\n")
            answer = next(lines)
        conn.close()
        return answer

    def open_gate(self, sid: str) -> Path:
        """Open the save socket of `sid` on the loop, as the start of a game does."""
        (self.root / sid / "work").mkdir(parents=True, exist_ok=True)
        opened = threading.Event()
        self.manager.schedule(lambda: (self.manager.gate_open(sid), opened.set()))
        self.case.assertTrue(opened.wait(5), f"launcher ended: {self.error!r}")
        return self.root / sid / "save.sock"

    def save(self, path: Path, line: bytes) -> dict:
        conn = self.connect(path)
        try:
            conn.sendall(line)
        except OSError:
            # Answered and closed before the whole line was taken: a broken
            # pipe or a reset on Linux, "socket is not connected" on macOS.
            pass
        return next(_lines(conn))


def _lines(conn: socket.socket):
    data = b""
    while True:
        while b"\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return
            data += chunk
        line, _, data = data.partition(b"\n")
        yield json.loads(line)


class LoopSurvivalTests(unittest.TestCase):
    def test_hostile_lines_on_both_sockets_are_refused_requests(self):
        with RunningLauncher(self) as launcher:
            gate = launcher.open_gate("s1")
            for line in (b"[" * 20000 + b"\n", b'{"type":"save","schema":1,"data":{"a":' + b"[" * 60000 + b"\n"):
                game = launcher.connect(gate)
                game.sendall(line)
            shell = launcher.connect(launcher.socket)
            shell.sendall(b"1\n[]\nnull\n" + b"[" * 4000 + b"\n")
            self.assertEqual(launcher.save(gate, b'{"type":"save","schema":1,"data":{"n":1}}\n')["error"]["code"],
                             "no_session", "a request after them is still answered (no session in this fixture)")
            launcher.alive()

    def test_a_callback_that_raises_does_not_take_the_others_or_the_loop_with_it(self):
        with RunningLauncher(self) as launcher:
            ran = threading.Event()
            launcher.manager.schedule(lambda: 1 / 0)
            launcher.manager.schedule(ran.set)
            self.assertTrue(ran.wait(5), f"launcher ended: {launcher.error!r}")
            launcher.alive()
            trace = [line for line in launcher.lines if "ZeroDivisionError" in line]
            self.assertEqual(len(trace), 1)
            self.assertIn("Traceback", trace[0])

    def test_a_tick_that_keeps_raising_is_logged_once_and_requests_are_still_served(self):
        with RunningLauncher(self) as launcher:
            ticks = []

            def tick():
                ticks.append(1)
                raise RuntimeError("tick step broke")
            launcher.manager.tick = tick
            launcher.wait(lambda: len(ticks) >= 5, "the loop stopped ticking")
            launcher.alive()
            self.assertEqual(len([line for line in launcher.lines if "tick step broke" in line]), 1)

    def test_a_request_that_raises_is_answered_as_failed(self):
        with RunningLauncher(self) as launcher:
            launcher.manager.launch = unittest.mock.Mock(side_effect=KeyError("slot"))
            answer = launcher.shell_request({"type": "launch", "slot": "c1", "serial": "NPT-x", "version": "1"})
            self.assertEqual((answer["type"], answer["accepted"], answer["error"]["code"]),
                             ("launch_result", False, "launcher_error"))
            gate = launcher.open_gate("s1")
            launcher.manager.save_request = unittest.mock.Mock(side_effect=KeyError("slot"))
            answer = launcher.save(gate, b'{"type":"save","schema":1,"data":{"n":1}}\n')
            self.assertEqual((answer["type"], answer["ok"], answer["error"]["code"]), ("saved", False, "save_failed"))
            launcher.alive()

    def test_a_session_that_cannot_be_adopted_is_ended_and_the_launcher_starts(self):
        launcher = RunningLauncher(self)
        launcher.session_left_running("sbroken")
        with unittest.mock.patch.object(launchd.SessionManager, "adopt", side_effect=RuntimeError("adopt broke")), launcher:
            launcher.alive()
            self.assertEqual(launcher.manager.results.current()["reason"], "interrupted")
            self.assertFalse((launcher.root / "sbroken").exists())


class AdoptionTests(unittest.TestCase):
    """A launcher that starts while a game runs (after a restart of any kind)."""

    def test_the_adopted_game_can_save_again_through_its_socket(self):
        launcher = RunningLauncher(self)
        launcher.session_left_running("sadopt1")
        with launcher:
            self.assertEqual((launcher.manager.state, launcher.manager.session["id"]), ("running", "sadopt1"))
            answer = launcher.save(launcher.root / "sadopt1" / "save.sock", b'{"type":"save","schema":1,"data":{"n":1}}\n')
            self.assertEqual((answer["type"], answer["error"]["code"]), ("saved", "reader_unavailable"),
                             "served by this launcher (no card service in this fixture)")

    def test_a_launcher_that_finds_no_game_opens_no_socket_and_brings_the_shell_back(self):
        with RunningLauncher(self) as launcher:
            self.assertEqual(launcher.manager.state, "idle")
            self.assertEqual([path.name for path in launcher.root.iterdir() if path.is_dir()], [])
            self.assertIn(["systemctl", "start", "--no-block", launchd.SHELL_UNIT], launcher.commands)


def still_open(conn: socket.socket) -> bool:
    """Whether the launcher still holds its end of `conn` (nothing was sent to it)."""
    conn.setblocking(False)
    try:
        return conn.recv(1) != b""
    except BlockingIOError:
        return True
    except OSError:
        return False


class ConnectionBoundTests(unittest.TestCase):
    SAVE = b'{"type":"save","schema":1,"data":{"n":1}}\n'

    def test_idle_connections_to_a_save_socket_are_bounded_and_a_save_still_gets_in(self):
        with RunningLauncher(self) as launcher:
            gate = launcher.open_gate("s1")
            idle = [launcher.connect(gate) for _ in range(60)]
            self.assertEqual(launcher.save(gate, self.SAVE)["type"], "saved")
            launcher.alive()
            self.assertLessEqual(sum(still_open(conn) for conn in idle), launchd.SAVE_CLIENTS_MAX)

    def test_a_connection_that_never_sends_its_request_is_closed(self):
        with RunningLauncher(self, SAVE_REQUEST_TIMEOUT=0.2) as launcher:
            gate = launcher.open_gate("s1")
            silent, slow = launcher.connect(gate), launcher.connect(gate)
            slow.sendall(self.SAVE[:10])                       # a line that never ends is no request either
            started = time.monotonic()
            self.assertEqual((silent.recv(1), slow.recv(1)), (b"", b""), "closed by the launcher, nothing sent")
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(launcher.save(gate, self.SAVE)["type"], "saved")

    def test_a_connection_gets_one_answer(self):
        with RunningLauncher(self) as launcher:
            conn = launcher.connect(launcher.open_gate("s1"))
            conn.sendall(self.SAVE)
            answers = _lines(conn)
            self.assertEqual(next(answers)["type"], "saved")
            try:
                conn.sendall(self.SAVE)
            except OSError:
                pass
            self.assertEqual(list(answers), [])

    def test_a_save_is_judged_by_its_size_however_its_line_spells_it(self):
        with RunningLauncher(self) as launcher:
            gate = launcher.open_gate("s1")
            text = {"text": "漢" * (20 * 1024)}                       # 60 KiB as the card stores it
            escaped = json.dumps({"type": "save", "schema": 1, "data": text}).encode("ascii") + b"\n"
            self.assertGreater(len(escaped), 120 * 1024, "twice that with every character escaped")
            self.assertEqual(launcher.save(gate, escaped)["error"]["code"], "no_session",
                             "read as a request (there is no session in this fixture)")
            endless = b'{"type":"save","schema":1,"data":{"t":"' + b"a" * (launchd.SAVE_LINE_MAX + 4096)
            answer = launcher.save(gate, endless)
            self.assertEqual((answer["type"], answer["ok"], answer["error"]["code"]), ("saved", False, "save_too_large"),
                             "a line no save can need is answered, not only closed")

    def test_clients_of_the_shell_socket_are_bounded_and_the_first_ones_stay(self):
        with RunningLauncher(self) as launcher:
            first = launcher.connect(launcher.socket)
            answers = _lines(first)
            self.assertEqual(next(answers)["type"], "snapshot")
            crowd = [launcher.connect(launcher.socket) for _ in range(launchd.CLIENTS_MAX + 20)]
            first.sendall(b'{"type":"status"}\n')
            self.assertEqual(next(answers)["type"], "snapshot", "an established client is still served")
            time.sleep(0.2)
            self.assertLessEqual(sum(still_open(conn) for conn in crowd), launchd.CLIENTS_MAX - 1)
            for conn in crowd:
                conn.close()
            launcher.alive()                                   # and there is room again once they leave


class StepFailureTests(unittest.TestCase):
    """The pieces a step is made of keep their own failures."""

    start, write = directory_saves.SessionTests.start, directory_saves.SessionTests.write

    def test_a_result_that_cannot_be_written_is_still_the_result(self):
        folder = Path(tempfile.mkdtemp(prefix="ml"))
        self.addCleanup(shutil.rmtree, folder, True)
        store = launchd.ResultStore(folder / "gone" / "last-result.json")     # as on a full launch file system
        store.store({"session": "s1", "reason": "exited"})
        self.assertEqual(store.current()["reason"], "exited")
        store.acknowledge()
        self.assertTrue(store.current()["acknowledged"])

    def test_an_unexpected_failure_of_a_capture_is_that_captures_failure(self):
        h, sid = self.start()
        with unittest.mock.patch.object(launchd, "unit_check", side_effect=RuntimeError("check broke")):
            self.write(h, "save-0000.sav", directory_saves.SLOT)
            h.advance(0.5)
            self.assertEqual(h.m.state, "running")
            self.assertEqual(h.m.sync.last_error["code"], "save_failed")
            self.write(h, "save-0000.sav", directory_saves.SLOT)
            h.runner.finish(sid, code=0)
            h.advance(0.5)
            h.pump()
        self.assertEqual(h.m.state, "idle", "the session ends without waiting for the save deadline")
        self.assertFalse(h.result()["saves"]["ok"])
        self.assertIn("La última partida no se guardó", h.result()["saves"]["message"])

    def test_text_that_cannot_be_encoded_does_not_keep_a_message_from_the_shell(self):
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        server = launchd.Server(Path("unused"), None)
        server.clients[ours] = None
        server.broadcast({"type": "session", "title": "half a pair: \ud83d"})
        theirs.settimeout(2)
        self.assertEqual(json.loads(theirs.recv(4096))["type"], "session")
        self.assertIn(ours, server.clients)


if __name__ == "__main__":
    unittest.main()
