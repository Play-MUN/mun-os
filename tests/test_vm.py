"""Host-side lifecycle regression tests; no VM, network or QEMU installation needed."""

import argparse
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "munvm", Path(__file__).resolve().parents[1] / "vm" / "munvm.py")
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)


REAL_RELEASE_IN_GUEST = vm.release_in_guest   # LabTests.setUp stubs it; one test needs the real thing


class Wire:
    """Deliver real JSON framing in arbitrary event/response order."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.timeout = 30.0
        self.sent = []

    def recv(self, _size):
        if not self.frames:
            raise socket.timeout()
        return json.dumps(self.frames.pop(0)).encode() + b"\r\n"

    def sendall(self, data):
        self.sent.append(json.loads(data))

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout


def client(frames):
    qmp = vm.Qmp.__new__(vm.Qmp)
    qmp.sock = Wire(frames)
    qmp.buf = b""
    qmp.events = []
    return qmp


def deleted(device):
    return {"event": "DEVICE_DELETED", "data": {"device": device}}


class QmpTests(unittest.TestCase):
    def test_deletion_before_command_response_is_retained(self):
        qmp = client([deleted("card-demo-dev"), {"return": {}}])
        qmp.execute("device_del", id="card-demo-dev")
        self.assertEqual(qmp.wait_event("DEVICE_DELETED", 0,
                                       {"device": "card-demo-dev"}), deleted("card-demo-dev"))

    def test_deletion_after_response_matches_requested_device(self):
        qmp = client([{"return": {}}, deleted("card-other-dev"), deleted("card-demo-dev")])
        qmp.execute("device_del", id="card-demo-dev")
        self.assertEqual(qmp.wait_event("DEVICE_DELETED", 1,
                                       {"device": "card-demo-dev"}), deleted("card-demo-dev"))
        self.assertEqual(qmp.wait_event("DEVICE_DELETED", 0,
                                       {"device": "card-other-dev"}), deleted("card-other-dev"))
        self.assertEqual(qmp.sock.gettimeout(), 30)

    def test_timeout_retains_unrelated_events_and_restores_socket_timeout(self):
        qmp = client([deleted("other")])
        self.assertIsNone(qmp.wait_event("DEVICE_DELETED", 0.1, {"device": "demo"}))
        self.assertEqual(qmp.sock.gettimeout(), 30)
        self.assertEqual(qmp.wait_event("DEVICE_DELETED", 0, {"device": "other"}), deleted("other"))

    def test_command_error_does_not_erase_queued_events(self):
        qmp = client([deleted("other"), {"error": {"desc": "device missing"}}])
        with self.assertRaisesRegex(vm.LabError, "device missing"):
            qmp.execute("device_del", id="demo")
        self.assertEqual(qmp.wait_event("DEVICE_DELETED", 0), deleted("other"))


class LabTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        paths = {name: self.root / name for name in vm.PATHS}
        for key in ("system_disk", "efivars"):
            paths[key].touch()
        self.enterContext(patch.object(vm, "PATHS", paths))
        self.enterContext(patch.object(vm, "CARD_ROOT", self.root / "cards"))
        self.enterContext(patch.object(vm, "ATTACH_REGISTRY", self.root / "cards" / "attached.json"))
        self.enterContext(patch.object(vm, "INSTANCE", "one"))
        self.enterContext(patch.object(vm, "release_in_guest", lambda serial: None))
        self.enterContext(patch.object(vm, "guest_card_records", side_effect=vm.LabError("no guest in host tests")))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.qmp = Mock()
        self.enterContext(patch.object(vm, "Qmp", return_value=self.qmp))
        self.pid = self.enterContext(patch.object(vm, "read_pid", return_value=123))
        self.run = self.enterContext(patch.object(vm, "run"))
        self.enterContext(patch.object(vm, "which", side_effect=lambda name: name))
        self.enterContext(patch.object(vm, "firmware_path", return_value=self.root / "firmware"))
        # The Mac these regressions were written on: HVF, UNIX sockets, -daemonize.
        self.enterContext(patch.object(vm, "HOST", vm.host.Host("macos", "arm64", hypervisor=True)))
        self.enterContext(patch.object(vm.host, "qemu_version", return_value=(11, 1, 1)))
        self.enterContext(patch.object(vm.host, "qemu_offers", return_value=["none", "cocoa", "coreaudio", "wav"]))
        self.card = {"slot": "card-slot-1", "node": "card-demo", "path": "demo.img"}
        vm.save_state({"cards": {}})

    def enterContext(self, context):
        # Keep tests runnable with the macOS system Python 3.9.
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def attach_image(self, name="demo"):
        vm.CARD_ROOT.mkdir(exist_ok=True)
        vm.card_path(name).touch()

    def test_restart_clears_stale_cards_and_allows_reattach(self):
        self.attach_image()
        vm.save_state({"cards": {"demo": self.card}, "created": "then"})
        self.pid.return_value = None
        vm.cmd_start(argparse.Namespace(display="none", print_command=False, wait=0))
        self.assertEqual(vm.load_state()["cards"], {})
        self.assertEqual(vm.load_state()["created"], "then", "only the cards are cleared")
        self.assertTrue(vm.card_path("demo").exists())
        self.pid.return_value = 123
        vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertIn("demo", vm.load_state()["cards"])

    def test_failed_start_leaves_no_phantom_cards(self):
        vm.save_state({"cards": {"demo": self.card}})
        self.pid.return_value = None
        self.run.side_effect = vm.LabError("start failed")
        with self.assertRaises(vm.LabError):
            vm.cmd_start(argparse.Namespace(display="none", print_command=False, wait=0))
        self.assertEqual(vm.load_state()["cards"], {})

    def test_print_command_has_no_state_or_socket_side_effects(self):
        vm.save_state({"cards": {"demo": self.card}})
        self.pid.return_value = None
        vm.PATHS["qmp"].touch()
        vm.PATHS["pidfile"].touch()
        before = vm.PATHS["state"].read_bytes()
        vm.cmd_start(argparse.Namespace(display="none", print_command=True, wait=0))
        self.assertEqual(vm.PATHS["state"].read_bytes(), before)
        self.assertTrue(vm.PATHS["qmp"].exists())
        self.assertTrue(vm.PATHS["pidfile"].exists())
        self.run.assert_not_called()

    def test_stop_clears_cards_only_after_process_exits(self):
        vm.save_state({"cards": {"demo": self.card}})
        self.pid.side_effect = [123, None, None]   # the last: the capture check re-confirms QEMU is gone
        vm.cmd_stop(argparse.Namespace(hard=False, timeout=1))
        self.assertEqual(vm.load_state()["cards"], {})

    def test_stop_timeout_preserves_attached_state(self):
        vm.save_state({"cards": {"demo": self.card}})
        with self.assertRaises(vm.LabError):
            vm.cmd_stop(argparse.Namespace(hard=False, timeout=0))
        self.assertIn("demo", vm.load_state()["cards"])

    def stale_capture(self, payload=b"\x01\x02" * 500):
        import struct
        header = (b"RIFF" + b"\0\0\0\0" + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
                  + b"data" + b"\0\0\0\0")
        vm.PATHS["audio"].write_bytes(header + payload)
        return len(payload)

    def declared_data_bytes(self):
        return int.from_bytes(vm.PATHS["audio"].read_bytes()[40:44], "little")

    def test_stop_on_an_already_stopped_guest_finalises_the_capture(self):
        payload = self.stale_capture()
        self.pid.return_value = None
        vm.cmd_stop(argparse.Namespace(hard=False, timeout=1))
        self.assertEqual(self.declared_data_bytes(), payload)

    def test_foreground_start_finalises_the_capture_when_qemu_returns(self):
        payload = self.stale_capture()
        self.pid.return_value = None
        vm.cmd_start(argparse.Namespace(display="window", print_command=False, wait=0, audio="wav"))
        self.assertEqual(self.declared_data_bytes(), payload)
        self.assertIn("wav,id=snd0", " ".join(self.run.call_args[0][0]))

    def test_capture_is_left_alone_while_qemu_is_alive(self):
        self.stale_capture()
        with self.assertRaises(vm.LabError):
            vm.cmd_stop(argparse.Namespace(hard=False, timeout=0))   # pid stays 123
        self.assertEqual(self.declared_data_bytes(), 0)
        self.pid.side_effect = [123, None, None]
        vm.cmd_stop(argparse.Namespace(hard=False, timeout=1))       # gone now: finalised
        self.assertNotEqual(self.declared_data_bytes(), 0)

    def test_stopped_status_does_not_report_phantom_cards(self):
        vm.save_state({"cards": {"demo": self.card}})
        self.pid.return_value = None
        with contextlib.redirect_stdout(io.StringIO()) as output:
            vm.cmd_status(argparse.Namespace())
        self.assertEqual(json.loads(output.getvalue())["attached_cards"], {})

    def test_device_add_failure_releases_backend_without_marking_attached(self):
        self.attach_image()
        def execute(command, **kwargs):
            if command == "device_add":
                raise vm.LabError("slot unavailable")
        self.qmp.execute.side_effect = execute
        with self.assertRaisesRegex(vm.LabError, "slot unavailable"):
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.qmp.execute.assert_any_call("blockdev-del", **{"node-name": "card-demo"})
        self.assertEqual(vm.load_state()["cards"], {})

    def test_backend_add_failure_does_not_delete_existing_backend(self):
        self.attach_image()
        self.qmp.execute.side_effect = vm.LabError("node already exists")
        with self.assertRaises(vm.LabError):
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertEqual(self.qmp.execute.call_count, 1)
        self.assertEqual(vm.load_state()["cards"], {})

    def test_detach_timeout_preserves_state_until_device_disappears(self):
        vm.save_state({"cards": {"demo": self.card}})
        self.qmp.execute.return_value = [{"name": "card-demo-dev"}]
        self.qmp.wait_event.return_value = None
        with self.assertRaisesRegex(vm.LabError, "pending"):
            vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=0))
        self.assertIn("demo", vm.load_state()["cards"])
        self.qmp.wait_event.assert_called_once_with(
            "DEVICE_DELETED", timeout=0, data_match={"device": "card-demo-dev"})

    def test_detach_retry_cleans_backend_after_event_connection_is_gone(self):
        vm.save_state({"cards": {"demo": self.card}})
        def execute(command, **kwargs):
            return [{"node-name": "card-demo"}] if command == "query-named-block-nodes" else []
        self.qmp.execute.side_effect = execute
        vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=1))
        self.qmp.wait_event.assert_not_called()
        self.qmp.execute.assert_any_call("blockdev-del", **{"node-name": "card-demo"})
        self.assertEqual(vm.load_state()["cards"], {})

    def test_busy_device_error_keeps_state_for_a_later_retry(self):
        vm.save_state({"cards": {"demo": self.card}})
        def execute(command, **kwargs):
            if command == "qom-list":
                return [{"name": "card-demo-dev"}]
            if command == "device_del":
                raise vm.LabError("guest is busy")
            self.fail("busy device must not trigger backend cleanup")
        self.qmp.execute.side_effect = execute
        with self.assertRaisesRegex(vm.LabError, "busy"):
            vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=1))
        self.qmp.wait_event.assert_not_called()
        self.assertIn("demo", vm.load_state()["cards"])

    def test_backend_cleanup_failure_preserves_retry_state(self):
        vm.save_state({"cards": {"demo": self.card}})
        def execute(command, **kwargs):
            if command == "blockdev-del":
                raise vm.LabError("busy backend")
            return [{"node-name": "card-demo"}] if command == "query-named-block-nodes" else []
        self.qmp.execute.side_effect = execute
        with self.assertRaisesRegex(vm.LabError, "busy backend"):
            vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=1))
        self.assertIn("demo", vm.load_state()["cards"])

    def test_force_create_rejects_attached_card(self):
        self.attach_image()
        vm.save_state({"cards": {"demo": self.card}})
        with self.assertRaisesRegex(vm.LabError, "attached"):
            vm.cmd_card_create(argparse.Namespace(name="demo", force=True, size="1G"))
        self.run.assert_not_called()

    def test_serials_preserve_names_and_report_actual_guest_path(self):
        serials = [vm.card_serial(name) for name in ("chapter01", "chapter02", "a" * 16)]
        self.assertEqual(len(set(serials)), 3)
        self.assertTrue(all(len(serial.encode("ascii")) <= 20 for serial in serials))
        self.attach_image("chapter01")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            vm.cmd_card_attach(argparse.Namespace(name="chapter01"))
        call = next(call for call in self.qmp.execute.call_args_list if call.args[0] == "device_add")
        serial = call.kwargs["serial"]
        self.assertIn("/dev/disk/by-id/virtio-" + serial, output.getvalue())
        self.assertEqual(vm.load_state()["cards"]["chapter01"]["serial"], serial)

    def test_invalid_card_names_and_link_targets_are_rejected(self):
        for name in ("", "../disk", "foo/bar", "A", "á", "a" * 17, "a,b", "a b"):
            with self.subTest(name=name), self.assertRaises(vm.LabError):
                vm.card_path(name)
        vm.CARD_ROOT.mkdir()
        (vm.CARD_ROOT / "demo.img").symlink_to(self.root / "missing-target")
        with self.assertRaisesRegex(vm.LabError, "regular file"):
            vm.card_path("demo")


class SavesLabTests(unittest.TestCase):
    """Save rules: one writer per card image, safe detach, separate guests."""

    setUp = LabTests.setUp
    enterContext = LabTests.enterContext
    attach_image = LabTests.attach_image

    def test_attach_is_refused_while_another_live_instance_holds_the_card(self):
        self.attach_image()
        vm.save_registry({"demo": {"instance": "two", "pid": 4242, "since": "now"}})
        with patch.object(vm, "pid_alive", return_value=True):
            with self.assertRaises(vm.LabError) as ctx:
                vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertIn("instance two", str(ctx.exception))
        self.qmp.execute.assert_not_called()
        self.assertEqual(vm.load_state()["cards"], {})

    def test_stale_registry_entry_of_a_dead_guest_is_replaced(self):
        self.attach_image()
        vm.save_registry({"demo": {"instance": "two", "pid": 4242, "since": "then"}})
        with patch.object(vm, "pid_alive", return_value=False):
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertEqual(vm.load_registry()["demo"]["instance"], "one")

    def test_own_entries_are_released_on_detach_and_cleared_on_start(self):
        self.attach_image()
        vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertEqual(vm.load_registry()["demo"]["instance"], "one")
        self.qmp.execute.side_effect = lambda cmd, **kw: [] if cmd in ("qom-list", "query-named-block-nodes") else None
        vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=1, abrupt=False))
        self.assertNotIn("demo", vm.load_registry())
        vm.save_registry({"demo": {"instance": "one", "pid": 1, "since": "x"}, "other": {"instance": "two", "pid": 2, "since": "x"}})
        vm.clear_cards()
        self.assertEqual(list(vm.load_registry()), ["other"], "only this instance's entries are cleared")

    def test_failed_attach_does_not_keep_a_claim(self):
        self.attach_image()
        self.qmp.execute.side_effect = lambda cmd, **kw: (_ for _ in ()).throw(vm.LabError("busy")) if cmd == "device_add" else None
        with self.assertRaises(vm.LabError):
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertNotIn("demo", vm.load_registry())

    def test_reattach_while_unplug_is_pending_explains_and_reconciles_once_gone(self):
        self.attach_image()
        vm.save_state({"cards": {"demo": self.card}})
        self.qmp.execute.side_effect = lambda command, **kw: [{"name": "card-demo-dev"}] if command == "qom-list" else []
        with patch.object(vm, "UNPLUG_GRACE", 0.0), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(vm.LabError) as ctx:
                vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertIn("still plugged in", str(ctx.exception))
        self.assertIn("demo", vm.load_state()["cards"], "a pending removal keeps the record")
        # The removal completes while we wait: the second presence check says gone, and the attach proceeds.
        presence = iter([[{"name": "card-demo-dev"}], []])
        def execute_wait(command, **kw):
            if command == "qom-list": return next(presence, [])
            if command == "query-named-block-nodes": return []
            return {}
        self.qmp.execute.side_effect = execute_wait
        with patch.object(vm, "UNPLUG_GRACE", 5.0), patch.object(vm.time, "sleep", lambda s: None), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertIn("waiting for the removal", out.getvalue())
        self.assertIn("demo", vm.load_state()["cards"], "attached again once the removal finished")
        vm.save_state({"cards": {"demo": self.card}})
        # Gone from QEMU (the watcher's device_del completed after the lab process ended): reconcile and attach again.
        calls = []
        def execute(command, **kw):
            calls.append(command)
            if command == "qom-list": return []
            if command == "query-named-block-nodes": return [{"node-name": "card-demo"}]
            if command == "device_add": return {}
            return {}
        self.qmp.execute.side_effect = execute
        with contextlib.redirect_stdout(io.StringIO()) as out:
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertIn("reconciled", out.getvalue())
        self.assertIn("blockdev-del", calls)
        self.assertIn("demo", vm.load_state()["cards"], "attached again after the reconciliation")

    def test_detach_of_a_card_the_console_released_does_not_ask_again(self):
        self.attach_image()
        vm.save_state({"cards": {"demo": self.card}})
        self.qmp.execute.side_effect = lambda command, **kw: [] if command in ("query-named-block-nodes",) else {}
        with patch.object(vm, "device_present", return_value=False), \
                patch.object(vm, "release_in_guest", side_effect=AssertionError("must not ask the guest again")):
            text = vm.detach_card("demo", verified_released=True)
        self.assertIn("after the console released it", text)
        self.assertNotIn("demo", vm.load_state()["cards"])

    def test_safe_detach_asks_the_guest_first_and_abrupt_does_not(self):
        self.attach_image()
        vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.qmp.execute.side_effect = lambda cmd, **kw: [] if cmd in ("qom-list", "query-named-block-nodes") else None
        asked = []
        with patch.object(vm, "release_in_guest", side_effect=lambda serial: asked.append(serial)):
            vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=1, abrupt=False))
        self.assertEqual(asked, ["NPT-demo"])
        vm.cmd_card_attach(argparse.Namespace(name="demo"))
        with patch.object(vm, "release_in_guest", side_effect=AssertionError("must not be asked")):
            vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=1, abrupt=True))

    def test_guest_refusal_keeps_the_card_attached(self):
        self.attach_image()
        vm.cmd_card_attach(argparse.Namespace(name="demo"))
        with patch.object(vm, "release_in_guest", REAL_RELEASE_IN_GUEST), \
                patch.object(vm, "guest_command", return_value=Mock(returncode=1, stdout='{"type": "released", "ok": false, "error": {"code": "in_use", "message": "sesión en curso"}}\n')):
            with self.assertRaises(vm.LabError) as ctx:
                vm.cmd_card_detach(argparse.Namespace(name="demo", timeout=1, abrupt=False))
        self.assertIn("in_use", str(ctx.exception)); self.assertIn("Exit the game first", str(ctx.exception))
        self.assertIn("demo", vm.load_state()["cards"], "still attached: nothing was unplugged")
        self.assertIn("demo", vm.load_registry())

class AudioTests(unittest.TestCase):
    def setUp(self):
        # A command line only: no QEMU installation is consulted.
        for name, replacement in (("which", patch.object(vm, "which", side_effect=lambda name: name)),
                                  ("firmware", patch.object(vm, "firmware_path", return_value=Path("/firmware"))),
                                  ("host", patch.object(vm, "HOST", vm.host.Host("macos", "arm64", hypervisor=True))),
                                  ("offers", patch.object(vm.host, "qemu_offers", return_value=["none", "coreaudio", "wav"]))):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_no_audio_when_off_and_virtio_sound_otherwise(self):
        plain = vm.qemu_command("none")
        self.assertFalse(any("audiodev" in part or "virtio-sound" in part for part in plain))
        self.assertEqual(vm.qemu_command("none", audio="off"), plain, "off removes the device on purpose")
        self.assertEqual(vm.build_parser().parse_args(["start"]).audio, "none", "a guest has a sound device by default")
        with_audio = vm.qemu_command("none", audio="none")
        self.assertIn("none,id=snd0", with_audio)
        self.assertIn("virtio-sound-pci,audiodev=snd0,streams=1", with_audio)
        self.assertEqual(with_audio[:len(plain)], plain, "audio only appends; the rest of the command is unchanged")

    def test_unfinished_wav_capture_gets_its_sizes_and_a_finished_one_is_left_alone(self):
        import struct
        tmp = Path(tempfile.mkdtemp(prefix="mun-wav-")); self.addCleanup(shutil.rmtree, tmp, True)
        pcm = b"\x01\x02" * 1000
        header = b"RIFF" + b"\0\0\0\0" + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16) + b"data" + b"\0\0\0\0"
        unfinished = tmp / "audio.wav"; unfinished.write_bytes(header + pcm)
        self.assertTrue(vm.finalize_wav(unfinished))
        fixed = unfinished.read_bytes()
        self.assertEqual(struct.unpack("<I", fixed[4:8])[0], len(fixed) - 8)
        self.assertEqual(struct.unpack("<I", fixed[40:44])[0], len(pcm))
        self.assertFalse(vm.finalize_wav(unfinished), "already consistent: untouched")
        stale = tmp / "stale.wav"; stale.write_bytes(header[:40] + struct.pack("<I", 200) + pcm)
        self.assertTrue(vm.finalize_wav(stale), "a header describing an earlier moment is corrected too")
        self.assertEqual(struct.unpack("<I", stale.read_bytes()[40:44])[0], len(pcm))
        (tmp / "not.wav").write_bytes(b"hello")
        self.assertFalse(vm.finalize_wav(tmp / "not.wav"))
        self.assertFalse(vm.finalize_wav(tmp / "missing.wav"))

    def test_wav_capture_lands_in_the_instance_directory_and_unknown_backends_are_refused(self):
        wav = vm.qemu_command("none", audio="wav")
        self.assertIn(f"wav,id=snd0,path={vm.PATHS['audio']}", wav)
        with self.assertRaises(vm.LabError):
            vm.audio_arguments("pulseaudio")


class FakeQMP:
    """QEMU as far as card attach/detach can tell: devices, block nodes and a
    DEVICE_DELETED wait, with hooks to run another command at the two moments
    the review found (paused in the wait, and after QEMU let go of the card)."""

    def __init__(self):
        self.devices = {"card-demo-dev"}
        self.nodes = {"card-demo"}
        self.on_wait = None
        self.on_close = None
        self.deleted = []
        self.lock = threading.Lock()

    def execute(self, command, **args):
        with self.lock:
            if command == "qom-list":
                return [{"name": name} for name in sorted(self.devices)]
            if command == "query-named-block-nodes":
                return [{"node-name": name} for name in sorted(self.nodes)]
            if command == "blockdev-add":
                self.nodes.add(args["node-name"])
            elif command == "device_add":
                self.devices.add(args["id"])
            elif command == "device_del":
                self.deleted.append(args["id"])
                self.devices.discard(args["id"])
            elif command == "blockdev-del":
                assert args["node-name"] + "-dev" not in self.devices, "backend dropped under a live device"
                self.nodes.discard(args["node-name"])
            return {}

    def wait_event(self, *args, **kwargs):
        if self.on_wait:
            callback, self.on_wait = self.on_wait, None
            callback()
        return {"event": "DEVICE_DELETED", "data": {"device": "card-demo-dev"}}

    def close(self):
        if self.on_close:
            callback, self.on_close = self.on_close, None
            callback()


class LifecycleFixture(unittest.TestCase):
    """A real guest state directory and registry, fake QEMU, no guest."""

    enterContext = LabTests.enterContext

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-lifecycle-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        cards = self.root / "cards"; cards.mkdir()
        for name in ("demo", "other"):
            (cards / f"{name}.img").touch()
        self.enterContext(patch.object(vm, "PATHS", {name: self.root / name for name in vm.PATHS}))
        self.enterContext(patch.object(vm, "CARD_ROOT", cards))
        self.enterContext(patch.object(vm, "ATTACH_REGISTRY", cards / "attached.json"))
        self.enterContext(patch.object(vm, "INSTANCE", "one"))
        self.enterContext(patch.object(vm, "read_pid", return_value=123))
        self.qmp = FakeQMP()
        self.enterContext(patch.object(vm, "Qmp", return_value=self.qmp))
        self.release = self.enterContext(patch.object(vm, "release_in_guest",
                                                      side_effect=AssertionError("the watcher never asks for a release")))
        self.records = []
        self.enterContext(patch.object(vm, "guest_card_records", side_effect=lambda: list(self.records)))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        vm.save_state({"cards": {"demo": {"slot": "card-slot-1", "node": "card-demo", "path": str(cards / "demo.img"),
                                          "serial": "NPT-demo", "attachment": "old", "stale_insertions": [],
                                          "insertion": None}}})
        vm.registry_claim("demo", "old")
        self.thread_errors = []

    def watch(self, polls=1):
        """Run the watcher for this many polls; each reconciles the guest's live records."""
        stop = threading.Event()
        announced, done = [], []
        reconcile = vm.reconcile_released
        def counted(announce):
            try:
                return reconcile(announce)
            finally:
                done.append(1)
                if len(done) >= polls:
                    stop.set()
        with patch.object(vm, "reconcile_released", side_effect=counted), patch.object(vm, "RELEASE_POLL_INTERVAL", 0.0):
            vm.watch_released(stop, announce=announced.append)
        return announced

    def attach_in_thread(self, name):
        def run():
            try:
                vm.cmd_card_attach(argparse.Namespace(name=name))
            except BaseException as exc:   # surfaced by the test, not lost in the thread
                self.thread_errors.append(exc)
        thread = threading.Thread(target=run, name=f"attach-{name}")
        thread.start()
        return thread


class LifecycleConcurrencyTests(LifecycleFixture):
    """Attach and detach are one transaction each, across processes."""

    def test_attach_during_a_paused_detach_waits_and_both_changes_survive(self):
        seen = {}
        def during_wait():
            self.thread = self.attach_in_thread("other")
            time.sleep(0.4)
            seen["blocked"] = self.thread.is_alive()
            seen["plugged_early"] = "card-other-dev" in self.qmp.devices
        self.qmp.on_wait = during_wait
        vm.detach_card("demo", verified_released=True, expected_attachment="old")
        self.thread.join(10)
        self.assertEqual(self.thread_errors, [])
        self.assertTrue(seen["blocked"], "the attach waited for the detach's transaction")
        self.assertFalse(seen["plugged_early"])
        self.assertEqual(self.qmp.devices, {"card-other-dev"})
        self.assertEqual(sorted(vm.load_state()["cards"]), ["other"])
        self.assertEqual(sorted(vm.load_registry()), ["other"])
        self.assertEqual(vm.load_registry()["other"]["attachment"], vm.load_state()["cards"]["other"]["attachment"])

    def test_same_card_reattached_after_qemu_let_go_keeps_its_new_record(self):
        def after_release():
            self.thread = self.attach_in_thread("demo")
            time.sleep(0.4)
        self.qmp.on_close = after_release
        vm.detach_card("demo", verified_released=True, expected_attachment="old")
        self.thread.join(10)
        self.assertEqual(self.thread_errors, [])
        self.assertEqual(self.qmp.devices, {"card-demo-dev"})
        entry = vm.load_state()["cards"]["demo"]
        self.assertNotEqual(entry["attachment"], "old", "a new insertion has a new identity")
        self.assertEqual(vm.load_registry()["demo"]["attachment"], entry["attachment"])

    def test_cleanup_of_an_old_attachment_never_removes_a_newer_record(self):
        # Even if a newer record appeared by a path that bypassed the lock, the
        # old detach removes only the attachment it started with.
        def newer_record_appears():
            state = vm.load_state()
            state["cards"]["demo"] = dict(state["cards"]["demo"], attachment="new")
            vm.save_state(state)
            vm.save_registry({"demo": {"instance": "one", "pid": 123, "since": "now", "attachment": "new"}})
        self.qmp.on_close = newer_record_appears
        vm.detach_card("demo", verified_released=True, expected_attachment="old")
        self.assertEqual(vm.load_state()["cards"]["demo"]["attachment"], "new")
        self.assertEqual(vm.load_registry()["demo"]["attachment"], "new")

    def test_a_separate_process_holding_the_lock_excludes_this_one(self):
        lock = vm.PATHS["lifecycle_lock"]
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import fcntl, os, sys, time\n"
                                   f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT, 0o644)\n"
                                   "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                                   "print('locked', flush=True)\n"
                                   "time.sleep(1.5)\n"],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        with self.assertRaises(vm.LabError) as ctx:
            with vm.lifecycle_lock(timeout=0.3):
                pass
        self.assertIn("busy", str(ctx.exception))
        started = time.monotonic()
        with patch.object(vm, "LOCK_TIMEOUT", 10.0):
            vm.cmd_card_attach(argparse.Namespace(name="other"))
        self.assertGreater(time.monotonic() - started, 0.5, "the attach waited for the other process")
        self.assertIn("other", vm.load_state()["cards"])

    def test_concurrent_writers_leave_valid_json_and_no_shared_temporary(self):
        def write(i):
            with vm.lifecycle_lock():
                state = vm.load_state(); state[f"k{i}"] = i; vm.save_state(state)
            vm.registry_claim(f"card{i}", str(i))
        threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        state = vm.load_state()
        self.assertEqual(sorted(k for k in state if k.startswith("k")), sorted(f"k{i}" for i in range(12)))
        self.assertEqual(sorted(n for n in vm.load_registry() if n.startswith("card")), sorted(f"card{i}" for i in range(12)))
        self.assertEqual([p.name for p in self.root.rglob("*.tmp")], [], "every temporary was renamed into place")

    def test_attach_records_the_insertions_the_guest_already_had_for_the_serial(self):
        vm.detach_card("demo", verified_released=True, expected_attachment="old")
        self.records = [{"serial": "NPT-demo", "insertion": "ins-before", "state": "released"}]
        vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertEqual(vm.load_state()["cards"]["demo"]["stale_insertions"], ["ins-before"])
        vm.detach_card("demo", verified_released=True)
        with patch.object(vm, "guest_card_records", side_effect=vm.LabError("qemu-ga down")):
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertIsNone(vm.load_state()["cards"]["demo"]["stale_insertions"], "unknown, not empty")


class ReleaseAuthorityTests(LifecycleFixture):
    """Only the current insertion's live released state, read under
    the lifecycle lock, lets the watcher unplug."""

    def test_an_earlier_plugs_release_leaves_the_playing_card_alone(self):
        # The current insertion is valid and a game is running on it; the guest
        # also still lists the earlier plug's released record.
        state = vm.load_state()
        state["cards"]["demo"].update(stale_insertions=["ins-old"], attachment="cur")
        vm.save_state(state)
        self.records = [{"serial": "NPT-demo", "insertion": "ins-old", "state": "released"},
                        {"serial": "NPT-demo", "insertion": "ins-cur", "state": "valid", "active": True}]
        self.watch()
        self.assertEqual(self.qmp.deleted, [], "an old release never unplugs a later insertion")
        self.assertIn("demo", vm.load_state()["cards"])
        self.assertEqual(vm.load_state()["cards"]["demo"]["insertion"], "ins-cur", "the current insertion was learned")
        self.release.assert_not_called()

    def test_repeated_polls_unplug_once(self):
        self.records = [{"serial": "NPT-demo", "insertion": "ins-1", "state": "released"}]
        announced = self.watch(2)
        self.assertEqual(self.qmp.deleted, ["card-demo-dev"])
        self.assertEqual(vm.load_state()["cards"], {})
        self.assertEqual(vm.load_registry(), {})
        self.assertTrue(any("after the console released it" in text for text in announced))

    def test_release_missed_while_the_guest_was_unreachable_is_found_by_a_later_poll(self):
        records = [{"serial": "NPT-demo", "insertion": "ins-1", "state": "released"}]
        answers = iter([vm.LabError("qemu-ga down"), records])
        def guest_card_records():
            answer = next(answers, records)
            if isinstance(answer, Exception):
                raise answer
            return list(answer)
        with patch.object(vm, "guest_card_records", side_effect=guest_card_records):
            self.watch(2)
        self.assertEqual(self.qmp.deleted, ["card-demo-dev"])

    def test_a_card_attached_while_the_guest_was_unreachable_is_bound_only_by_what_was_learned(self):
        state = vm.load_state()
        state["cards"]["demo"]["stale_insertions"] = None
        vm.save_state(state)
        self.records = [{"serial": "NPT-demo", "insertion": "ins-x", "state": "released"}]
        self.watch()
        self.assertEqual(self.qmp.deleted, [], "never learned: an unbound released record is not authority")
        self.records = [{"serial": "NPT-demo", "insertion": "ins-y", "state": "valid"}]
        self.watch()
        self.assertEqual(vm.load_state()["cards"]["demo"]["insertion"], "ins-y")
        self.records = [{"serial": "NPT-demo", "insertion": "ins-y", "state": "released"}]
        self.watch()
        self.assertEqual(self.qmp.deleted, ["card-demo-dev"])

    def test_guest_that_cannot_be_asked_means_no_unplug(self):
        with patch.object(vm, "guest_card_records", side_effect=vm.LabError("qemu-ga down")):
            announced = self.watch()
        self.assertEqual(self.qmp.deleted, [])
        self.assertTrue(any("could not reconcile" in text for text in announced))

    def test_release_check_and_unplug_hold_the_lock_against_a_concurrent_attach(self):
        self.records = [{"serial": "NPT-demo", "insertion": "ins-1", "state": "released"}]
        seen = {}
        def during_wait():
            self.thread = self.attach_in_thread("other")
            time.sleep(0.4)
            seen["blocked"] = self.thread.is_alive()
        self.qmp.on_wait = during_wait
        self.watch()
        self.thread.join(10)
        self.assertEqual(self.thread_errors, [])
        self.assertTrue(seen["blocked"])
        self.assertEqual(sorted(vm.load_state()["cards"]), ["other"])


class ClosureFollowupTests(LifecycleFixture):
    """Re-attaching a card that is still plugged in, and binding a blind attachment."""

    def test_attach_that_wins_the_lock_pulls_a_released_card_itself_and_reinserts_it(self):
        # The guest released the card, the watcher has not unplugged
        # it yet, and a same-card attach takes the lifecycle lock first.
        self.records = [{"serial": "NPT-demo", "insertion": "ins-1", "state": "released"}]
        execute = self.qmp.execute
        watchers, errors = [], []
        def watcher():
            try:
                vm.reconcile_released(lambda message: None)
            except BaseException as exc:
                errors.append(exc)
        def start_watcher_once_attach_holds_the_lock(command, **args):
            if command == "qom-list" and not watchers:
                thread = threading.Thread(target=watcher); watchers.append(thread); thread.start()
            return execute(command, **args)
        started = time.monotonic()
        with patch.object(self.qmp, "execute", side_effect=start_watcher_once_attach_holds_the_lock), \
                patch.object(vm, "UNPLUG_GRACE", 2.0):
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        for thread in watchers:
            thread.join(5); self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertLess(time.monotonic() - started, 1.5, "no waiting for a removal the attach itself performs")
        self.assertEqual(self.qmp.deleted, ["card-demo-dev"], "pulled exactly once")
        self.assertEqual(self.qmp.devices, {"card-demo-dev"}, "and inserted again")
        entry = vm.load_state()["cards"]["demo"]
        self.assertNotEqual(entry["attachment"], "old")
        self.assertEqual(entry["stale_insertions"], ["ins-1"], "the released record belongs to the earlier plug")
        self.assertEqual(vm.load_registry()["demo"]["attachment"], entry["attachment"])

    def test_attach_refuses_a_card_that_is_playing_at_once_and_pulls_nothing(self):
        self.records = [{"serial": "NPT-demo", "insertion": "ins-1", "state": "valid", "active": True}]
        started = time.monotonic()
        with self.assertRaises(vm.LabError) as ctx:
            vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertLess(time.monotonic() - started, 1.0, "refused now, not after the grace")
        self.assertIn("game running", str(ctx.exception))
        self.assertEqual(self.qmp.deleted, [])
        self.assertEqual(vm.load_state()["cards"]["demo"]["attachment"], "old")
        self.assertEqual(vm.load_registry()["demo"]["attachment"], "old")

    def test_attach_waiting_for_an_unprovable_removal_does_not_hold_the_lock(self):
        # Guest unreachable: nothing proves the old insertion was released, so the
        # attach waits; meanwhile another transaction must be able to run.
        results = []
        with patch.object(vm, "guest_card_records", side_effect=vm.LabError("qemu-ga down")), \
                patch.object(vm, "UNPLUG_GRACE", 5.0):
            thread = threading.Thread(target=lambda: results.append(
                vm.cmd_card_attach(argparse.Namespace(name="demo"))))
            thread.start()
            time.sleep(0.4)
            with vm.lifecycle_lock(timeout=1.0):
                self.qmp.devices.discard("card-demo-dev")   # the pending removal completes
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [None])
        self.assertEqual(self.qmp.devices, {"card-demo-dev"})
        self.assertNotEqual(vm.load_state()["cards"]["demo"]["attachment"], "old")

    def test_attach_while_still_unprovable_after_the_grace_fails_without_pulling(self):
        with patch.object(vm, "guest_card_records", side_effect=vm.LabError("qemu-ga down")), \
                patch.object(vm, "UNPLUG_GRACE", 0.3):
            with self.assertRaises(vm.LabError) as ctx:
                vm.cmd_card_attach(argparse.Namespace(name="demo"))
        self.assertIn("has not shown that its insertion was released", str(ctx.exception))
        self.assertEqual(self.qmp.deleted, [])
        self.assertIn("demo", vm.load_state()["cards"])

    def blind_attachment(self):
        state = vm.load_state()
        state["cards"]["demo"].update(stale_insertions=None, insertion=None)
        vm.save_state(state)

    def test_card_attached_blind_is_bound_while_valid_then_unplugged_on_release(self):
        # Guest unreachable at attach, the watcher's first poll sees
        # no card, the card becomes valid and is released between later polls.
        self.blind_attachment()
        answers = iter([[], [{"serial": "NPT-demo", "insertion": "ins-new", "state": "valid"}],
                        [{"serial": "NPT-demo", "insertion": "ins-new", "state": "released"}]])
        with patch.object(vm, "guest_card_records", side_effect=lambda: list(next(answers))):
            self.watch(3)
        self.assertEqual(self.qmp.deleted, ["card-demo-dev"], "bound while valid, then unplugged")
        self.assertEqual(vm.load_state()["cards"], {})

    def test_released_record_never_seen_valid_is_still_refused_and_reported_once(self):
        self.blind_attachment()
        self.records = [{"serial": "NPT-demo", "insertion": "ins-x", "state": "released"}]
        announced = self.watch(3)
        self.assertEqual(self.qmp.deleted, [], "the guard against unbound released records stands")
        notices = [text for text in announced if "never saw that insertion" in text]
        self.assertEqual(len(notices), 1, announced)
        self.assertIn("card-detach demo", notices[0])

    def test_polls_report_an_unreachable_card_service_once(self):
        self.blind_attachment()
        with patch.object(vm, "guest_card_records", side_effect=vm.LabError("cardd_unavailable")):
            announced = self.watch(3)
        self.assertEqual(len([t for t in announced if "could not reconcile" in t]), 1, announced)
        self.assertEqual(self.qmp.deleted, [])


class MouseTests(unittest.TestCase):
    def test_pointer_events_scale_to_the_tablet_range_and_click_is_press_then_release(self):
        move_only = vm.mouse_events(0.5, 1.0, click=False)
        self.assertEqual(move_only, [[{"type": "abs", "data": {"axis": "x", "value": 16384}},
                                      {"type": "abs", "data": {"axis": "y", "value": 32767}}]])
        clicked = vm.mouse_events(0.0, 0.0, click=True)
        self.assertEqual(len(clicked), 3)
        self.assertEqual([e[0]["data"].get("down") for e in clicked[1:]], [True, False])
        with self.assertRaises(vm.LabError):
            vm.mouse_events(1.2, 0.5, click=False)


class DownloadTests(unittest.TestCase):
    """The verified download vm/mundev.py uses for the builder's base image."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-image-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.content = b"small verified image fixture"
        self.digest = hashlib.sha512(self.content).hexdigest()

    def test_download_validates_before_publishing_image(self):
        target = self.root / "base.qcow2"
        with patch.object(vm.urllib.request, "urlopen", return_value=io.BytesIO(b"bad data")):
            with self.assertRaisesRegex(vm.LabError, "pinned"):
                vm.download("https://example.invalid/base", target, self.digest)
        self.assertFalse(target.exists())
        with patch.object(vm.urllib.request, "urlopen", return_value=io.BytesIO(self.content)):
            vm.download("https://example.invalid/base", target, self.digest)
        self.assertEqual(target.read_bytes(), self.content)

    def test_download_never_overwrites_an_existing_backing_file(self):
        target = self.root / "base.qcow2"
        target.write_bytes(b"keep existing backing")
        with patch.object(vm.urllib.request, "urlopen", return_value=io.BytesIO(self.content)):
            with self.assertRaises(FileExistsError):
                vm.download("https://example.invalid/base", target, self.digest)
        self.assertEqual(target.read_bytes(), b"keep existing backing")



class ImageGuestTests(unittest.TestCase):
    """An image guest (a MUN OS development build): no network, qemu-ga only."""

    def enterContext(self, context):
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-guest-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.enterContext(patch.object(vm, "GUESTS_ROOT", self.root))
        (self.root / "one").mkdir()
        (self.root / "one" / "guest.json").write_text('{"build": "b1"}')
        self.enterContext(patch.object(vm, "which", side_effect=lambda name: name))
        self.enterContext(patch.object(vm, "firmware_path", return_value=Path("/fw.fd")))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        for name in ("PATHS", "INSTANCE", "GUEST_HOSTNAME"):
            value = vm.PATHS.copy() if name == "PATHS" else getattr(vm, name)
            self.enterContext(patch.object(vm, name, value))

    def test_an_image_guest_is_selected_by_name_and_has_its_own_paths(self):
        vm.select_instance("one")
        self.assertEqual((vm.INSTANCE, vm.GUEST_HOSTNAME), ("one", "mun-one"))
        self.assertEqual(vm.PATHS["system_disk"], self.root / "one" / "system.qcow2")
        self.assertEqual(vm.PATHS["qga"].name, "qga.sock")
        for unknown in ("a", "c", "two", "../one", "A"):
            with self.assertRaises(vm.LabError):
                vm.select_instance(unknown)

    def test_the_guest_has_no_network_interface_and_only_the_agent_port(self):
        vm.select_instance("one")
        cmd = vm.qemu_command("none")
        joined = " ".join(cmd)
        self.assertEqual(cmd[cmd.index("-nic") + 1], "none")
        for absent in ("-netdev", "hostfwd", "virtio-net", "seed", "cidata"):
            self.assertNotIn(absent, joined)
        self.assertIn("name=org.qemu.guest_agent.0", joined)
        self.assertIn(f"path={vm.PATHS['qga']},server=on,wait=off", joined)
        self.assertIn("serial=MUN-SYSTEM", joined)
        self.assertNotIn("serial=NPT-", joined, "the system disk never looks like a card")
        self.assertEqual(sum(1 for part in cmd if part.startswith("pcie-root-port")), len(vm.CARD_SLOTS))
        self.assertIn("virtio-sound-pci,audiodev=snd0,streams=1", vm.qemu_command("none", audio="none"))

    def test_guest_commands_go_through_the_agent(self):
        vm.select_instance("one")
        with patch.object(vm, "qga_run", return_value=subprocess.CompletedProcess([], 0, "{}", "")) as qga:
            vm.guest_command("python3 /opt/mun/launchd/launchd.py cards")
        self.assertEqual(qga.call_args.args[0], ["/bin/sh", "-c", "python3 /opt/mun/launchd/launchd.py cards"])

    def test_the_watcher_polls_an_image_guest(self):
        vm.select_instance("one")
        stop = threading.Event()
        calls = []

        def reconcile(announce):
            calls.append(1)
            if len(calls) == 3:
                stop.set()
            return []

        with patch.object(vm, "read_pid", return_value=123), patch.object(vm, "reconcile_released", side_effect=reconcile), \
                patch.object(vm, "RELEASE_POLL_INTERVAL", 0.01), \
                patch.object(vm.subprocess, "Popen", side_effect=AssertionError("no journal to follow")):
            vm.watch_released(stop, announce=lambda text: None)
        self.assertEqual(len(calls), 3)

    def test_long_socket_paths_move_to_a_short_private_directory(self):
        deep = Path("/tmp") / ("d" * 120)
        path = vm.socket_path(deep, "qmp.sock")
        self.assertLessEqual(len(str(path).encode()), 104)
        self.assertEqual(path.name, "qmp.sock")
        self.assertEqual(path, vm.socket_path(deep, "qmp.sock"), "the same instance always gets the same path")
        self.assertNotEqual(path.parent, vm.socket_path(Path("/tmp") / ("e" * 120), "qmp.sock").parent)
        self.assertEqual(vm.socket_path(Path("/tmp/x"), "qmp.sock"), Path("/tmp/x/qmp.sock"))


class QgaWire:
    """qemu-ga's side of the socket: answers per request, optionally
    after bytes a previous client left behind."""

    def __init__(self, stale=b"", statuses=None):
        self.pending = stale
        self.sent = []
        self.statuses = list(statuses or [])

    def settimeout(self, timeout):
        pass

    def connect(self, path):
        pass

    def sendall(self, data):
        text = data.lstrip(b"\xff")
        message = json.loads(text)
        self.sent.append(message)
        name = message["execute"]
        if name == "guest-sync-delimited":
            self.pending += b'{"return": 1}\n' + b"\xff" + json.dumps({"return": message["arguments"]["id"]}).encode() + b"\n"
        elif name == "guest-exec":
            self.pending += b'{"return": {"pid": 42}}\n'
        elif name == "guest-exec-status":
            self.pending += json.dumps({"return": self.statuses.pop(0)}).encode() + b"\n"

    def recv(self, size):
        chunk, self.pending = self.pending[:size], self.pending[size:]
        return chunk

    def close(self):
        pass


class QgaTests(unittest.TestCase):
    def connect(self, wire):
        with tempfile.NamedTemporaryFile() as handle, patch.object(vm.socket, "socket", return_value=wire):
            return vm.Qga(Path(handle.name))

    def test_sync_skips_what_an_earlier_client_left_behind(self):
        wire = QgaWire(stale=b'{"return": {"pid": 7}}\n{"retu')
        self.connect(wire)
        self.assertEqual(wire.sent[0]["execute"], "guest-sync-delimited")

    def test_run_waits_for_exit_and_decodes_output(self):
        out = base64.b64encode(b'{"ok": true}\n').decode()
        wire = QgaWire(statuses=[{"exited": False}, {"exited": True, "exitcode": 3, "out-data": out,
                                                       "err-data": base64.b64encode(b"warn").decode()}])
        qga = self.connect(wire)
        with patch.object(vm.time, "sleep"):
            result = qga.run(["/bin/sh", "-c", "true"], timeout=5)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (3, '{"ok": true}\n', "warn"))
        request = next(m for m in wire.sent if m["execute"] == "guest-exec")
        self.assertEqual(request["arguments"]["path"], "/bin/sh")
        self.assertTrue(request["arguments"]["capture-output"])

    def test_a_killed_command_reports_its_signal(self):
        wire = QgaWire(statuses=[{"exited": True, "signal": 9}])
        result = self.connect(wire).run(["/bin/sleep", "100"], timeout=5)
        self.assertEqual(result.returncode, 137)

    def test_a_qga_error_is_a_lab_error(self):
        wire = QgaWire()
        qga = self.connect(wire)
        wire.pending = b'{"error": {"class": "GenericError", "desc": "command failed"}}\n'
        wire.sendall = lambda data: None
        with self.assertRaises(vm.LabError):
            qga.execute("guest-exec", path="/bin/true")

if __name__ == "__main__":
    unittest.main()
