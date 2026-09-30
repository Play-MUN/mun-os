"""State-machine tests for mun-launchd with fake systemd, shell, cards and staging."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mun-launchd"))

import launchd  # noqa: E402


def game_card(slot="c1", serial="NPT-game", version="0.1.0", kind="game", entry="content/mun-collect", state="valid",
              insertion="ins-1", card_id="mun.collect"):
    return {"slot": slot, "insertion": insertion, "serial": serial, "state": state, "active": True,
            "info": {"id": card_id, "title": "MUN Collect", "version": version, "kind": kind, "entry": entry}}


class FakeRunner:
    def __init__(self):
        self.dirs = set(); self.started = []; self.stopped = []; self.forgotten = []; self.status = {}
        self.fail_start = False

    def prepare_dir(self, sid):
        self.dirs.add(sid); return Path("/run/mun/launch") / sid

    def remove_dir(self, sid):
        self.dirs.discard(sid)

    def start_unit(self, sid, session):
        if self.fail_start:
            raise RuntimeError("systemd-run failed: no such user")
        self.started.append(sid)
        self.status[sid] = {"active": True, "finished": False, "result": None, "exit_code": None, "signal": None}

    def unit_status(self, sid):
        return self.status.get(sid, {"active": False, "finished": True, "result": "success", "exit_code": 0, "signal": None})

    def forget_unit(self, sid):
        self.forgotten.append(sid)

    def stop_unit(self, sid):
        self.stopped.append(sid)
        self.status[sid] = {"active": False, "finished": True, "result": "success", "exit_code": None, "signal": 15}

    def finish(self, sid, code=0, signal=None, result="success", started=True):
        self.status[sid] = {"active": False, "finished": True, "result": result, "exit_code": code, "signal": signal,
                            "started": started}

    # A cleanup unit that succeeded, unless a test says otherwise.
    DONE = {"load": "loaded", "state": "active", "substate": "exited", "result": "success"}

    def cleanup_status(self, sid):
        return getattr(self, "cleanup", {}).get(sid, self.DONE)

    def release_cleanup(self, sid, outcome):
        self.released = getattr(self, "released", []) + [(sid, outcome)]

    def list_cleanup_units(self):
        return list(getattr(self, "cleanup", {}))


class FakeShell:
    def __init__(self): self.log = []
    def stop(self): self.log.append("stop")
    def start(self): self.log.append("start")


class Harness:
    def __init__(self, card=None, stage_ok=True, stage_error=None):
        self.cards = launchd.CardView()
        self.cards.apply({"type": "snapshot", "cards": [card] if card else []})
        self.runner, self.shell = FakeRunner(), FakeShell()
        self.events, self.pending, self.stage_requests = [], [], []
        self.time = 100.0
        self.stage_ok, self.stage_error = stage_ok, stage_error
        self.auto_stage = True
        self.m = launchd.SessionManager(self.cards, self.stager, self.runner, self.shell, self.events.append,
                                        self.pending.append, clock=lambda: self.time)

    def stager(self, request, reply):
        self.stage_requests.append(request)
        if self.auto_stage:
            reply({"ok": True, "path": request["dest"] + "/game", "size": 707208, "sha256": "ab" * 32} if self.stage_ok
                  else {"ok": False, "error": self.stage_error or {"code": "entry_too_large", "message": "demasiado grande"}})

    def pump(self):
        while self.pending:
            self.pending.pop(0)()

    def advance(self, seconds):
        self.time += seconds
        self.m.tick()

    def result(self):
        return self.m.results.current()


class LaunchTests(unittest.TestCase):
    def test_happy_path_launch_run_exit_restores_shell(self):
        h = Harness(game_card())
        reply = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})
        self.assertTrue(reply["accepted"]); sid = reply["session"]
        self.assertEqual(h.m.state, "preparing")
        h.pump()   # staged
        self.assertEqual(h.m.state, "starting")
        self.assertEqual(h.shell.log, ["stop"]); self.assertEqual(h.runner.started, [sid])
        h.advance(0.5); self.assertEqual(h.m.state, "running")
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertEqual(h.m.state, "idle")
        self.assertEqual(h.shell.log, ["stop", "start"])
        self.assertNotIn(sid, h.runner.dirs, "session dir removed")
        self.assertEqual(h.result()["reason"], "exited")
        self.assertFalse(h.result()["acknowledged"])

    def test_rejections(self):
        h = Harness(game_card())
        self.assertEqual(h.m.launch({"slot": "c9", "serial": "NPT-game", "version": "0.1.0"})["error"]["code"], "card_mismatch")
        self.assertEqual(h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "9.9"})["error"]["code"], "card_mismatch")
        h2 = Harness(game_card(kind="test", entry=None))
        self.assertEqual(h2.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["error"]["code"], "not_runnable")
        h3 = Harness(None); h3.cards.connected = False
        self.assertEqual(h3.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["error"]["code"], "reader_unavailable")

    def test_duplicate_requests_are_busy(self):
        h = Harness(game_card())
        first = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})
        second = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})
        self.assertTrue(first["accepted"]); self.assertEqual(second["error"]["code"], "busy")
        self.assertEqual(len(h.stage_requests), 1)

    def test_card_removed_during_preparation_cancels_without_touching_shell(self):
        h = Harness(game_card()); h.auto_stage = False
        h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})
        h.cards.apply({"type": "removed", "slot": "c1"}); h.m.card_removed("c1")
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.result()["reason"], "card_removed")
        self.assertEqual(h.shell.log, ["start"], "shell was never stopped; start is harmless")
        self.assertEqual(h.runner.started, [])

    def test_stage_failure_reports_and_keeps_shell(self):
        h = Harness(game_card(), stage_ok=False, stage_error={"code": "entry_too_large", "message": "demasiado grande"})
        h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"}); h.pump()
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.result()["reason"], "entry_too_large")
        self.assertEqual(h.runner.started, [])

    def test_start_failure_restores_shell(self):
        h = Harness(game_card()); h.runner.fail_start = True
        h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"}); h.pump()
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.result()["reason"], "start_failed")
        self.assertEqual(h.shell.log, ["stop", "start"])

    def test_card_removed_while_running_stops_unit_then_restores(self):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        self.assertEqual(h.m.state, "running")
        h.cards.apply({"type": "removed", "slot": "c1"}); h.m.card_removed("c1")
        self.assertEqual(h.m.state, "stopping"); self.assertEqual(h.runner.stopped, [sid])
        h.advance(0.5)
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.result()["reason"], "card_removed")
        self.assertEqual(h.shell.log[-1], "start")

    def test_reader_lost_grace_then_stop(self):
        h = Harness(game_card())
        h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"}); h.pump(); h.advance(0.5)
        h.cards.connected = False; h.m.reader_disconnected()
        h.advance(2.0); self.assertEqual(h.m.state, "running", "within grace the game keeps running")
        h.advance(4.0); self.assertEqual(h.m.state, "stopping")
        h.advance(0.5); self.assertEqual(h.result()["reason"], "reader_lost")

    def test_reader_reconnect_without_card_ends_session(self):
        h = Harness(game_card())
        h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"}); h.pump(); h.advance(0.5)
        h.cards.connected = False; h.m.reader_disconnected()
        h.cards.apply({"type": "snapshot", "cards": []}); h.m.reader_reconnected()
        self.assertEqual(h.m.state, "stopping"); h.advance(0.5)
        self.assertEqual(h.result()["reason"], "card_removed")

    def test_crash_and_failure_results(self):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        h.runner.finish(sid, code=None, signal=9, result="signal"); h.advance(0.5)
        self.assertEqual(h.result()["reason"], "crashed"); self.assertEqual(h.result()["signal"], 9)
        self.assertEqual(h.runner.forgotten, [sid])  # failed unit reset only after its result was read
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        h.runner.finish(sid, code=3, result="exit-code"); h.advance(0.5)
        self.assertEqual(h.result()["reason"], "failed"); self.assertEqual(h.result()["code"], 3)

    def test_prepare_timeout(self):
        h = Harness(game_card()); h.auto_stage = False
        h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})
        h.advance(25.0)
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.result()["reason"], "prepare_failed")

    # --- R2: continuity is proven by the insertion token, never by the slot ----
    def running_session(self):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        self.assertEqual(h.m.state, "running"); self.assertEqual(h.m.session["insertion"], "ins-1")
        self.assertEqual(h.stage_requests[0]["insertion"], "ins-1", "cardd is asked to stage this very insertion")
        return h, sid

    def test_reconnect_with_another_card_in_the_reused_slot_ends_session(self):
        h, sid = self.running_session()
        h.cards.connected = False; h.m.reader_disconnected()
        h.cards.apply({"type": "snapshot", "cards": [game_card(insertion="ins-9", serial="NPT-other", version="9.9.9")]})
        h.m.reader_reconnected()
        self.assertEqual(h.m.state, "stopping"); self.assertEqual(h.runner.stopped, [sid])
        h.advance(0.5); self.assertEqual(h.result()["reason"], "card_removed")

    def test_reconnect_same_serial_but_other_content_ends_session(self):
        h, sid = self.running_session()
        h.cards.connected = False; h.m.reader_disconnected()
        for other in (game_card(insertion="ins-9", version="0.2.0"), game_card(insertion="ins-9", card_id="mun.other"),
                      game_card(insertion="ins-9", state="invalid")):
            h.cards.apply({"type": "snapshot", "cards": [other]})
            self.assertEqual(h.m._continuity(h.cards.active()), "other")
        h.m.reader_reconnected(); self.assertEqual(h.m.state, "stopping")

    def test_reconnect_after_reader_restart_reverifies_same_card(self):
        h, sid = self.running_session()
        h.cards.connected = False; h.m.reader_disconnected()
        # cardd restarted: same card rescanned as c1 with a fresh insertion token
        h.cards.apply({"type": "snapshot", "cards": [game_card(slot="c1", insertion="ins-2")]}); h.m.reader_reconnected()
        self.assertEqual(h.m.state, "running"); self.assertEqual(h.runner.stopped, [])
        self.assertEqual(h.m.session["insertion"], "ins-2", "session follows the re-verified insertion")
        h.cards.apply({"type": "removed", "slot": "c1", "insertion": "ins-2"}); h.m.card_removed("c1", "ins-2")
        self.assertEqual(h.m.state, "stopping", "the re-verified insertion is the one whose removal ends the session")

    def test_reconnect_while_reader_still_reading_waits_then_reverifies(self):
        h, sid = self.running_session()
        h.cards.connected = False; h.m.reader_disconnected(); h.advance(1.0)
        h.cards.apply({"type": "snapshot", "cards": [game_card(insertion="ins-2", state="reading")]}); h.m.reader_reconnected()
        self.assertEqual(h.m.state, "running", "a rescanned card is 'reading' first: no verdict yet")
        self.assertIsNotNone(h.m.reader_lost_at, "the wait stays bounded")
        h.advance(1.0)
        h.cards.apply({"type": "card", "card": game_card(insertion="ins-2")}); h.m.card_changed()
        self.assertIsNone(h.m.reader_lost_at); self.assertEqual(h.m.session["insertion"], "ins-2")
        h.advance(60.0); self.assertEqual(h.m.state, "running")

    def test_reconnect_with_card_that_never_validates_ends_at_the_deadline(self):
        h, sid = self.running_session()
        h.cards.connected = False; h.m.reader_disconnected(); h.advance(1.0)
        h.cards.apply({"type": "snapshot", "cards": [game_card(insertion="ins-2", state="reading")]}); h.m.reader_reconnected()
        h.advance(3.0); self.assertEqual(h.m.state, "running")
        h.advance(2.0); self.assertEqual(h.m.state, "stopping")
        h.advance(0.5); self.assertEqual(h.result()["reason"], "card_removed")

    def test_reconnect_with_reading_card_of_another_serial_ends_at_once(self):
        h, sid = self.running_session()
        h.cards.connected = False; h.m.reader_disconnected()
        h.cards.apply({"type": "snapshot", "cards": [game_card(insertion="ins-9", serial="NPT-demo", state="reading")]})
        h.m.reader_reconnected()
        self.assertEqual(h.m.state, "stopping")

    def test_removed_event_for_another_insertion_in_same_slot_is_ignored(self):
        h, sid = self.running_session()
        h.m.card_removed("c1", "ins-stale")
        self.assertEqual(h.m.state, "running")
        h.m.card_removed("c1", "ins-1")
        self.assertEqual(h.m.state, "stopping")

    # --- the identity of a pending copy is frozen until it starts ---------------
    def preparing_session(self, insertion="ins-1"):
        h = Harness(game_card(insertion=insertion)); h.auto_stage = False
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]
        self.assertEqual(h.m.state, "preparing")
        self.assertEqual(h.stage_requests[0]["insertion"], insertion)
        return h, sid, dict(h.stage_requests[0])

    def old_success(self, h, sid, request):
        """The staging reply for `request`, arriving after the reader moved on."""
        h.m._staged(sid, {"ok": True, "session": sid, "path": request["dest"] + "/game",
                          "size": 707504, "sha256": "ab" * 32})

    def test_reconnect_with_a_valid_rescanned_card_cancels_the_preparation(self):
        h, sid, request = self.preparing_session()
        h.cards.connected = False; h.m.reader_disconnected()
        h.cards.apply({"type": "snapshot", "cards": [game_card(insertion="ins-2")]})
        h.m.reader_reconnected()      # same serial/id/version, new insertion
        self.assertIsNone(h.m.session, "the session is gone, not re-pointed at ins-2")
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.result()["reason"], "prepare_failed")
        self.old_success(h, sid, request)
        self.assertEqual(h.runner.started, [], "a late success must not start the old copy")
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.result()["reason"], "prepare_failed")
        self.assertEqual(h.shell.log, ["start"], "the shell was never stopped while preparing")

    def test_reconnect_reading_then_valid_cancels_before_the_old_reply(self):
        h, sid, request = self.preparing_session()
        h.cards.connected = False; h.m.reader_disconnected()
        h.cards.apply({"type": "snapshot", "cards": [game_card(insertion="ins-2", state="reading")]})
        h.m.reader_reconnected()      # a rescanned card: not the insertion the copy was authorised against
        self.assertIsNone(h.m.session); self.assertEqual(h.result()["reason"], "prepare_failed")
        h.cards.apply({"type": "card", "card": game_card(insertion="ins-2")}); h.m.card_changed()
        self.old_success(h, sid, request)
        self.assertEqual(h.runner.started, []); self.assertEqual(h.m.state, "idle")
        self.assertEqual(h.shell.log, ["start"])

    def test_reconnect_with_the_same_insertion_keeps_preparing(self):
        # The launcher's own socket may drop while the card service stays up: the
        # copy is still authorised, so the session must survive and then start.
        h, sid, request = self.preparing_session()
        h.cards.connected = False; h.m.reader_disconnected()
        h.cards.apply({"type": "snapshot", "cards": [game_card(insertion="ins-1")]}); h.m.reader_reconnected()
        self.assertEqual(h.m.state, "preparing"); self.assertIsNone(h.m.reader_lost_at)
        self.old_success(h, sid, request)
        self.assertEqual(h.m.state, "starting"); self.assertEqual(h.runner.started, [sid])

    def test_staged_reply_is_refused_if_the_session_identity_moved(self):
        h, sid, request = self.preparing_session()
        h.m.session["insertion"] = "ins-2"    # no path does this; the guard is a backstop
        self.old_success(h, sid, request)
        self.assertEqual(h.runner.started, []); self.assertEqual(h.result()["reason"], "prepare_failed")

    def test_staged_copy_starts_only_for_the_staged_insertion(self):
        h = Harness(game_card()); h.auto_stage = False
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]
        request, reply = h.stage_requests[0], None
        # Between copy and start the reader re-registered a card in c1: same
        # serial/version, different insertion. Not proven continuity: no start.
        h.cards.apply({"type": "card", "card": game_card(insertion="ins-2")})
        h.stager_reply = None
        h.m._staged(sid, {"ok": True, "path": request["dest"] + "/game", "size": 1, "sha256": "ab" * 32})
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.runner.started, [])
        self.assertEqual(h.result()["reason"], "card_removed"); self.assertEqual(h.shell.log, ["start"])

    def test_adopted_session_without_reader_ends_after_grace(self):
        # R1: after a launcher restart the reader may never come back; the adopted
        # game gets the same 5 s grace as a supervised one, then stops.
        h = Harness(None); h.cards.connected = False
        h.runner.status["sabc"] = {"active": True, "finished": False, "result": None, "exit_code": None, "signal": None}
        h.m.adopt("sabc", {"slot": "c1", "serial": "NPT-game", "card_id": "mun.collect", "title": "MUN Collect", "version": "0.1.0"})
        self.assertIsNotNone(h.m.reader_lost_at, "grace armed at adoption while disconnected")
        h.advance(2.0); self.assertEqual(h.m.state, "running")
        h.advance(4.0); self.assertEqual(h.m.state, "stopping"); self.assertEqual(h.runner.stopped, ["sabc"])
        h.advance(0.5); self.assertEqual(h.result()["reason"], "reader_lost")

    def test_adopted_session_reader_back_within_grace_keeps_running(self):
        h = Harness(None); h.cards.connected = False
        h.runner.status["sabc"] = {"active": True, "finished": False, "result": None, "exit_code": None, "signal": None}
        h.m.adopt("sabc", {"slot": "c1", "serial": "NPT-game", "card_id": "mun.collect", "title": "MUN Collect", "version": "0.1.0"})
        h.advance(2.0)
        h.cards.apply({"type": "snapshot", "cards": [game_card()]}); h.m.reader_reconnected()
        self.assertIsNone(h.m.reader_lost_at)
        h.advance(60.0); self.assertEqual(h.m.state, "running"); self.assertEqual(h.runner.stopped, [])

    def test_adopt_survivor_and_acknowledge(self):
        h = Harness(game_card())
        h.m.adopt("sabc", {"slot": "c1", "serial": "NPT-game", "card_id": "mun.collect", "title": "MUN Collect", "version": "0.1.0"})
        self.assertEqual(h.m.state, "running")
        h.runner.status["sabc"] = {"active": True, "finished": False, "result": None, "exit_code": None, "signal": None}
        h.runner.finish("sabc", code=0); h.advance(0.5)
        self.assertEqual(h.m.state, "idle"); self.assertFalse(h.result()["acknowledged"])
        h.m.acknowledge(); self.assertTrue(h.result()["acknowledged"])
        self.assertEqual(h.m.snapshot()["state"], "idle")


class FeedReaderTests(unittest.TestCase):
    """R3: cardd's feed arrives in arbitrary fragments and may carry lines over 1 MiB."""

    def test_fragmented_lines_are_reassembled_in_order(self):
        import json
        reader = launchd.LineReader()
        big = json.dumps({"type": "card", "card": {"slot": "c1", "cover": "x" * (1300 * 1024)}}) + "\n"
        stream = (json.dumps({"type": "snapshot", "cards": []}) + "\n" + big + "garbage\n"
                  + json.dumps({"type": "removed", "slot": "c1"}) + "\n").encode()
        messages = []
        for i in range(0, len(stream), 7777):
            messages += reader.feed(stream[i:i + 7777])
        self.assertEqual([m["type"] for m in messages], ["snapshot", "card", "removed"])
        self.assertEqual(len(messages[1]["card"]["cover"]), 1300 * 1024)
        self.assertEqual(bytes(reader.buffer), b"")

    def test_unfinished_line_over_budget_is_a_protocol_break(self):
        reader = launchd.LineReader(limit=1000)
        reader.feed(b"x" * 600)
        with self.assertRaises(launchd.FrameTooLarge):
            reader.feed(b"y" * 600)
        self.assertEqual(bytes(reader.buffer), b"", "buffer released; caller reconnects for a fresh snapshot")
        self.assertEqual(launchd.MAX_FRAME_BYTES, 2 * 1024 * 1024)


class SaveGateTests(unittest.TestCase):
    """Saves are accepted only from the running session and bound to its insertion (docs/saves.md)."""

    def running(self):
        h = Harness(game_card())
        h.saves = []
        h.m.saver = lambda request, reply: h.saves.append((request, reply))
        h.opened, h.closed = [], []
        h.m.gate_open = h.opened.append
        h.m.gate_close = lambda: h.closed.append(True)
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        self.assertEqual(h.m.state, "running"); self.assertEqual(h.opened, [sid], "socket opened before the game started")
        return h, sid

    def test_save_is_forwarded_bound_to_session_and_insertion(self):
        h, sid = self.running()
        replies = []
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {"x": 1}}, replies.append)
        request, reply = h.saves[-1]
        self.assertEqual((request["type"], request["session"], request["insertion"], request["serial"], request["game"], request["version"]),
                         ("save", sid, "ins-1", "NPT-game", "mun.collect", "0.1.0"))
        self.assertEqual(request["payload"], {"x": 1})
        self.assertEqual(replies, [], "no answer to the game before the card service has written")
        reply({"ok": True, "bytes": 120}); h.pump()
        self.assertEqual(replies, [{"type": "saved", "ok": True, "bytes": 120}])

    def test_save_refused_unless_running(self):
        h = Harness(game_card()); replies = []
        h.m.save_request("sx", {"type": "save", "schema": 1, "data": {}}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "no_session")
        h, sid = self.running(); replies = []
        h.m.save_request("other", {"type": "save", "schema": 1, "data": {}}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "no_session")
        h.cards.connected = False
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {}}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "reader_unavailable")

    def test_bad_requests_and_one_at_a_time(self):
        h, sid = self.running(); replies = []
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": "no"}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "save_invalid")
        h.m.save_request(sid, {"type": "save", "schema": 0, "data": {}}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "save_invalid")
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {"b": "x" * 70000}}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "save_too_large")
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {"a": 1}}, replies.append)
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {"a": 2}}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "save_busy")
        h.saves[-1][1]({"ok": False, "error": {"code": "no_space", "message": "lleno"}}); h.pump()
        self.assertEqual(replies[-1], {"type": "saved", "ok": False, "error": {"code": "no_space", "message": "lleno"}})
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {"a": 3}}, replies.append)
        self.assertEqual(len(h.saves), 2, "after the reply the next save is accepted")

    def test_late_reply_after_the_session_ended_is_not_delivered(self):
        h, sid = self.running(); replies = []
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {"a": 1}}, replies.append)
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.closed, [True], "socket closed with the session")
        h.saves[-1][1]({"ok": True, "bytes": 5}); h.pump()
        self.assertEqual(replies, [], "nobody to confirm to; nothing is confirmed")

    def test_release_is_refused_while_the_card_has_a_session(self):
        h, sid = self.running()
        self.assertEqual(h.m.release_request("NPT-game")["error"]["code"], "in_use")
        self.assertIsNone(h.m.release_request("NPT-other"))
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertIsNone(h.m.release_request("NPT-game"))

    def test_adopted_session_reopens_its_save_socket(self):
        h = Harness(game_card()); opened = []
        h.m.gate_open = opened.append
        h.runner.status["sabc"] = {"active": True, "finished": False, "result": None, "exit_code": None, "signal": None}
        h.m.adopt("sabc", {"slot": "c1", "insertion": "ins-1", "serial": "NPT-game", "card_id": "mun.collect", "title": "MUN Collect", "version": "0.1.0"})
        self.assertEqual(opened, ["sabc"])


if __name__ == "__main__":
    unittest.main()


class RuntimeProfileTests(unittest.TestCase):
    """The unit a game gets is decided by the manifest profile the validator accepted."""

    def test_framebuffer_profile_keeps_the_v0_unit(self):
        props = launchd.unit_properties("linux-arm64-v0", "s1", Path("/run/mun/launch/s1"))
        self.assertIn("DeviceAllow=/dev/fb0 rw", props); self.assertIn("DeviceAllow=char-input rw", props)
        self.assertNotIn("DeviceAllow=char-drm rw", props); self.assertNotIn("SupplementaryGroups=audio", props)
        self.assertIn("MemoryMax=512M", props); self.assertIn("DevicePolicy=closed", props)
        env = launchd.unit_environment("linux-arm64-v0", "s1", {"card_id": "mun.collect", "version": "0.1.0"}, Path("/x/s1"))
        self.assertEqual(env["MUN_RUNTIME_PROFILE"], "linux-arm64-v0")
        self.assertNotIn("SDL_VIDEODRIVER", env)
        self.assertEqual(env["MUN_SAVE_SOCKET"], "/x/s1/save.sock")

    def test_games_built_before_the_mun_naming_get_the_same_environment_under_neptune_names(self):
        # MUN Collect on existing cards was built to read NEPTUNE_SAVE_FILE and
        # NEPTUNE_SAVE_SOCKET (docs/game-cards.md); a game built now reads MUN_*.
        session = {"card_id": "mun.collect", "version": "0.1.0", "content": {"device": "/dev/vdc", "root": "content"}}
        env = launchd.unit_environment("linux-arm64-gl-v0", "s1", session, Path("/x/s1"))
        mun = {k[len("MUN_"):]: v for k, v in env.items() if k.startswith("MUN_")}
        neptune = {k[len("NEPTUNE_"):]: v for k, v in env.items() if k.startswith("NEPTUNE_")}
        self.assertEqual(mun, neptune, "one source, two names, the same values")
        self.assertEqual(set(mun), {"CARD_ID", "CONTENT_VERSION", "SESSION", "RUNTIME_PROFILE", "CONTENT_DIR",
                                    "SAVE_FILE", "SAVE_SOCKET"})
        self.assertEqual(neptune["SAVE_FILE"], "/x/s1/work/save.json")
        self.assertEqual(neptune["CONTENT_DIR"], "/run/mun/card/content")

    def test_cards_with_mun_names_get_the_mun_variables_only(self):
        # NEPTUNE_* exists for games on earlier cards (docs/game-cards.md); a card with
        # mun.toml carries games built for MUN_* alone.
        session = {"card_id": "mun.collect", "version": "0.1.0", "naming": "mun",
                   "content": {"device": "/dev/vdc", "root": "content"}}
        env = launchd.unit_environment("linux-arm64-gl-v0", "s1", session, Path("/x/s1"))
        self.assertFalse([k for k in env if k.startswith("NEPTUNE_")])
        self.assertEqual(env["MUN_SAVE_FILE"], "/x/s1/work/save.json")
        self.assertEqual(env["MUN_CONTENT_DIR"], "/run/mun/card/content")
        earlier = launchd.unit_environment("linux-arm64-v0", "s1", dict(session, naming="earlier"), Path("/x/s1"))
        self.assertEqual(earlier["NEPTUNE_SAVE_FILE"], earlier["MUN_SAVE_FILE"])

    def test_the_card_naming_travels_in_the_session_and_its_record(self):
        for naming, expected in (("mun", "mun"), ("earlier", "earlier"), (None, "earlier"), ("bogus", "earlier")):
            with self.subTest(naming=naming):
                card = game_card()
                if naming is not None:
                    card["info"]["naming"] = naming
                h = Harness(card)
                self.assertTrue(h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["accepted"])
                self.assertEqual(h.m.session["naming"], expected)
                record = json.loads(json.dumps(h.m.session))       # what session.json holds for adoption
                self.assertEqual(launchd.card_naming(record), expected)
        self.assertEqual(launchd.card_naming({}), "earlier", "a record from before the naming generations")

    def test_a_single_object_save_of_the_other_generation_is_reported_with_the_result(self):
        card = game_card(); card["info"]["naming"] = "mun"
        h = Harness(card); h.auto_stage = False
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]
        h.m._staged(sid, {"ok": True, "path": "x", "size": 1, "sha256": "ab" * 32,
                          "save": {"present": True, "copied": False,
                                   "error": {"code": "save_other_generation", "detail": "save.json es neptune-save/1"}}})
        self.assertEqual(h.m.state, "starting", "the game still starts, without the save")
        h.advance(0.5); h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertFalse(h.result()["saves"]["ok"])
        self.assertIn("no se usó", h.result()["saves"]["message"])

    def test_gl_profile_opens_drm_alsa_and_input_only_and_points_sdl_at_them(self):
        props = launchd.unit_properties("linux-arm64-gl-v0", "s2", Path("/run/mun/launch/s2"))
        allowed = [p for p in props if p.startswith("DeviceAllow=")]
        self.assertEqual(sorted(allowed), sorted(["DeviceAllow=char-drm rw", "DeviceAllow=char-alsa rw", "DeviceAllow=char-input rw"]))
        for common in ("CapabilityBoundingSet=", "PrivateNetwork=yes", "ProtectSystem=strict", "NoNewPrivileges=yes",
                       "DevicePolicy=closed", "ReadWritePaths=/run/mun/launch/s2/work"):
            self.assertIn(common, props)
        self.assertIn("SupplementaryGroups=render", props); self.assertIn("SupplementaryGroups=audio", props)
        env = launchd.unit_environment("linux-arm64-gl-v0", "s2", {}, Path("/x/s2"))
        self.assertEqual((env["SDL_VIDEODRIVER"], env["SDL_AUDIODRIVER"], env["ALSOFT_DRIVERS"]), ("kmsdrm", "alsa", "alsa"))
        self.assertEqual(env["HOME"], "/x/s2/work")

    def test_unknown_profile_is_refused_before_a_unit_exists(self):
        with self.assertRaises(RuntimeError):
            launchd.unit_properties("windows-v9", "s3", Path("/x/s3"))
        with self.assertRaises(RuntimeError):
            launchd.unit_environment(None, "s3", {}, Path("/x/s3"))

    def test_launch_records_the_card_profile_in_the_session(self):
        card = game_card(); card["info"]["profile"] = "linux-arm64-gl-v0"
        h = Harness(card)
        reply = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})
        self.assertTrue(reply["accepted"])
        self.assertEqual(h.m.session["profile"], "linux-arm64-gl-v0")
        self.assertEqual(h.stage_requests[0]["dest"], "/run/mun/launch/" + reply["session"], "staging is profile-independent")


class ContentAccessTests(unittest.TestCase):
    """A mount-access card gets its device mounted read-only at a fixed path inside the unit."""

    GRANT = {"device": "/dev/vdc", "root": "content"}

    def test_staged_content_grant_is_kept_mounted_and_announced(self):
        h = Harness(game_card()); h.auto_stage = False
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]
        h.m._staged(sid, {"ok": True, "path": "x", "size": 1, "sha256": "ab" * 32, "content": dict(self.GRANT, extra="ignored")})
        self.assertEqual(h.m.state, "starting")
        self.assertEqual(h.m.session["content"], self.GRANT)
        props = launchd.unit_properties("linux-arm64-gl-v0", sid, Path("/run/mun/launch") / sid, h.m.session["content"])
        self.assertIn("MountImages=-/dev/vdc:/run/mun/card:root:ro,nosuid,nodev,noexec", props)
        self.assertIn("DeviceAllow=/dev/vdc r", props)
        self.assertIn("DevicePolicy=closed", props)
        env = launchd.unit_environment("linux-arm64-gl-v0", sid, h.m.session, Path("/x"))
        self.assertEqual(env["MUN_CONTENT_DIR"], "/run/mun/card/content")
        self.assertEqual(env["NEPTUNE_CONTENT_DIR"], env["MUN_CONTENT_DIR"])

    def test_copy_access_has_no_mount_and_no_content_variable(self):
        h = Harness(game_card())
        reply = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"}); h.pump()
        self.assertIsNone(h.m.session["content"])
        props = launchd.unit_properties("linux-arm64-v0", reply["session"], Path("/x"), None)
        self.assertFalse(any(p.startswith("MountImages=") or p.endswith(" r") for p in props))
        env = launchd.unit_environment("linux-arm64-v0", reply["session"], h.m.session, Path("/x"))
        self.assertNotIn("MUN_CONTENT_DIR", env); self.assertNotIn("NEPTUNE_CONTENT_DIR", env)

    def test_content_grants_that_are_not_a_plain_device_and_safe_root_cancel_the_launch(self):
        bad = ["/run/mun/cards/c1/content", 7, {"device": "/dev/vdc:/etc", "root": "content"},
               {"device": "/dev/../etc", "root": "content"}, {"device": "/dev/vdc", "root": "../saves"},
               {"device": "/dev/vdc", "root": "/content"}, {"device": "/dev/vdc", "root": "content:x"}, {"device": "/dev/vdc"}]
        for grant in bad:
            h = Harness(game_card()); h.auto_stage = False
            sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]
            h.m._staged(sid, {"ok": True, "path": "x", "size": 1, "sha256": "ab" * 32, "content": grant})
            self.assertEqual(h.m.state, "idle", grant)
            self.assertEqual(h.result()["reason"], "prepare_failed", grant)
            self.assertEqual(h.runner.started, [], "no unit was started")
        self.assertEqual(launchd.content_grant({"device": "/dev/vdc", "root": "data/sample"}), {"device": "/dev/vdc", "root": "data/sample"})


class UnitStatusTests(unittest.TestCase):
    """systemctl show → session judgement; a queued unit is not a finished one."""

    def show(self, **props):
        base = {"ActiveState": "inactive", "SubState": "dead", "Result": "success", "ExecMainStatus": "0",
                "ExecMainCode": "0", "LoadState": "loaded", "ExecMainStartTimestampMonotonic": "0"}
        base.update(props)
        return "\n".join(f"{k}={v}" for k, v in base.items()) + "\n"

    def test_a_unit_whose_start_job_has_not_run_is_neither_active_nor_finished(self):
        status = launchd.parse_unit_status(self.show())
        self.assertFalse(status["active"]); self.assertFalse(status["finished"])

    def test_running_exited_crashed_and_setup_failure_are_judged_as_before(self):
        running = launchd.parse_unit_status(self.show(ActiveState="active", SubState="running", ExecMainStartTimestampMonotonic="123"))
        self.assertTrue(running["active"]); self.assertFalse(running["finished"])
        exited = launchd.parse_unit_status(self.show(ExecMainStartTimestampMonotonic="123", ExecMainCode="1", ExecMainStatus="0"))
        self.assertTrue(exited["finished"]); self.assertEqual(exited["exit_code"], 0)
        crashed = launchd.parse_unit_status(self.show(ActiveState="failed", Result="signal", ExecMainStartTimestampMonotonic="123",
                                                      ExecMainCode="2", ExecMainStatus="6"))
        self.assertTrue(crashed["finished"]); self.assertEqual(crashed["signal"], 6)
        setup_failed = launchd.parse_unit_status(self.show(ActiveState="failed", Result="exit-code", ExecMainCode="1", ExecMainStatus="226"))
        self.assertTrue(setup_failed["finished"], "no start timestamp, but Result says it failed"); self.assertEqual(setup_failed["exit_code"], 226)
        gone = launchd.parse_unit_status(self.show(LoadState="not-found"))
        self.assertTrue(gone["finished"])

    def test_a_queued_unit_keeps_the_session_starting_until_the_timeout(self):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump()
        h.runner.status[sid] = launchd.parse_unit_status(self.show())
        h.advance(0.5); self.assertEqual(h.m.state, "starting", "not yet started: keep waiting")
        self.assertIn(sid, h.runner.dirs, "the session directory must survive until the unit ran")
        h.runner.status[sid] = launchd.parse_unit_status(self.show(ActiveState="active", SubState="running", ExecMainStartTimestampMonotonic="9"))
        h.advance(0.5); self.assertEqual(h.m.state, "running")


class SessionEndingTests(unittest.TestCase):
    """Finding N1 (2026-09-26): the game's ending is judged from its own process;
    what failed around it (the unit, its cleanup) is reported apart, never
    folded into the game's result and never dropped."""

    def ended(self, **finish):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        h.runner.finish(sid, **finish); h.advance(0.5)
        self.assertEqual(h.m.state, "idle")
        return h, sid, h.result()

    def test_a_normal_exit_is_exited_without_a_platform_line(self):
        _, _, result = self.ended(code=0)
        self.assertEqual(result["reason"], "exited"); self.assertNotIn("platform", result)

    def test_exit_0_under_a_failed_unit_result_is_exited_and_the_failure_is_shown_apart(self):
        # What N1 looked like: the game exited 0, the unit ended exit-code
        # because a process of the console's around it failed.
        _, _, result = self.ended(code=0, result="exit-code")
        self.assertEqual((result["reason"], result["code"]), ("exited", 0), "the game ended normally")
        self.assertFalse(result["platform"]["ok"])
        self.assertIn("La consola no cerró la sesión con normalidad (systemd: exit-code)", result["platform"]["message"])

    def test_crashes_nonzero_exits_and_start_failures_stay_what_they_are(self):
        self.assertEqual(self.ended(code=None, signal=11, result="signal")[2]["reason"], "crashed")
        self.assertEqual(self.ended(code=3, result="exit-code")[2]["reason"], "failed")
        _, _, never = self.ended(code=0, result="resources", started=False)
        self.assertEqual(never["reason"], "start_failed", "a unit that never ran did not end with the game's code 0")
        self.assertIn("resources", never["detail"])

    def test_a_failed_cleanup_unit_is_reported_with_the_result(self):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        h.runner.cleanup = {sid: {"state": "failed", "result": "exit-code"}}
        h.runner.finish(sid, code=0); h.advance(0.5)
        result = h.result()
        self.assertEqual(result["reason"], "exited")
        self.assertIn("La limpieza de la sesión falló (systemd: exit-code)", result["platform"]["message"])
        self.assertNotIn(sid, h.runner.dirs, "and the launcher removed the session itself")

    def test_a_finished_cleanup_is_released_and_reports_nothing(self):
        h, sid, result = self.ended(code=0)
        self.assertNotIn("platform", result)
        self.assertEqual(h.runner.released, [(sid, "succeeded")])
        self.assertEqual(h.m.cleanups, {})

    def test_status_parsing_keeps_the_game_and_the_unit_apart(self):
        show = UnitStatusTests().show
        exit0_unit_failed = launchd.parse_unit_status(show(ActiveState="failed", Result="exit-code", ExecMainCode="1",
                                                           ExecMainStatus="0", ExecMainStartTimestampMonotonic="77"))
        self.assertEqual((exit0_unit_failed["exit_code"], exit0_unit_failed["signal"], exit0_unit_failed["started"],
                          exit0_unit_failed["result"]), (0, None, True, "exit-code"))
        never = launchd.parse_unit_status(show(ActiveState="failed", Result="resources"))
        self.assertIs(never["started"], False)
        dumped = launchd.parse_unit_status(show(ActiveState="failed", Result="core-dump", ExecMainCode="3",
                                                ExecMainStatus="11", ExecMainStartTimestampMonotonic="5"))
        self.assertEqual((dumped["signal"], dumped["exit_code"]), (11, None), "a core dump is a crash, not exit code 11")
        self.assertIsNone(launchd.parse_unit_status(show(LoadState="not-found"))["started"])


RUNNING = {"load": "loaded", "state": "activating", "substate": "start", "result": "success"}
FAILED = {"load": "loaded", "state": "failed", "substate": "failed", "result": "exit-code"}


class CleanupOutcomeTests(unittest.TestCase):
    """N1 follow-up (2026-09-26): a cleanup still running when the session ends
    is watched until it succeeds, fails or times out, and whatever it says goes
    with that session's result, never another's."""

    def session_ending_with_cleanup(self, cleanup=RUNNING):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        h.runner.cleanup = {sid: dict(cleanup)}
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertEqual(h.m.state, "idle", "the session is over: the shell does not wait for its cleanup")
        self.assertEqual(h.shell.log[-1], "start")
        self.assertNotIn("platform", h.result())
        self.assertIn(sid, h.m.cleanups)
        return h, sid

    def test_pending_then_success(self):
        h, sid = self.session_ending_with_cleanup()
        h.advance(0.5)
        self.assertIn(sid, h.m.cleanups, "still running: still watched")
        h.runner.cleanup[sid] = dict(FakeRunner.DONE)
        h.advance(0.5)
        self.assertEqual(h.m.cleanups, {})
        self.assertEqual(h.runner.released, [(sid, "succeeded")])
        self.assertNotIn("platform", h.result())

    def test_pending_then_failure_reaches_that_sessions_result_and_is_shown_again(self):
        h, sid = self.session_ending_with_cleanup()
        h.m.acknowledge()                        # the player already dismissed the result
        h.runner.cleanup[sid] = dict(FAILED)
        events = len(h.events)
        h.advance(0.5)
        result = h.result()
        self.assertEqual((result["session"], result["reason"]), (sid, "exited"), "still the game's own ending")
        self.assertIn("La limpieza de la sesión falló (systemd: exit-code)", result["platform"]["message"])
        self.assertFalse(result["acknowledged"], "new information: shown again")
        self.assertEqual(h.events[-1]["last_result"]["platform"]["code"], "cleanup_failed")
        self.assertGreater(len(h.events), events)
        self.assertEqual(h.runner.released, [(sid, "failed")])
        self.assertEqual(h.m.cleanups, {})

    def test_pending_then_timeout(self):
        h, sid = self.session_ending_with_cleanup()
        h.advance(launchd.CLEANUP_TIMEOUT - 1)
        self.assertNotIn("platform", h.result())
        h.advance(2)
        self.assertIn(f"no terminó en {int(launchd.CLEANUP_TIMEOUT)} s", h.result()["platform"]["message"])
        self.assertEqual(h.runner.released, [(sid, "timeout")], "a cleanup past every bound is stopped")
        self.assertEqual(h.m.cleanups, {})
        h.advance(30)
        self.assertEqual(len(h.runner.released), 1, "reported once")

    def test_a_late_failure_never_lands_on_a_later_sessions_result(self):
        h, first = self.session_ending_with_cleanup()
        second = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        h.runner.finish(second, code=0); h.advance(0.5)
        self.assertEqual(h.result()["session"], second)
        h.runner.cleanup[first] = dict(FAILED)
        h.advance(0.5)
        self.assertEqual(h.result()["session"], second)
        self.assertNotIn("platform", h.result(), "the first session's notice is not the second's")
        self.assertEqual(h.m.cleanups, {})

    def test_a_late_failure_while_the_next_game_runs_goes_to_its_own_session(self):
        h, first = self.session_ending_with_cleanup()
        second = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        self.assertEqual(h.m.state, "running")
        h.runner.cleanup[first] = dict(FAILED)
        h.advance(0.5)
        self.assertEqual(h.result()["session"], first)
        self.assertIn("La limpieza de la sesión falló", h.result()["platform"]["message"])
        self.assertEqual(h.m.session["id"], second, "the running session is untouched")

    def test_a_session_that_never_started_a_unit_waits_for_no_cleanup(self):
        h = Harness(game_card(), stage_ok=False)
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump()
        h.runner.cleanup = {sid: {"load": "loaded", "state": "inactive", "substate": "dead", "result": "success"}}
        h.advance(launchd.CLEANUP_TIMEOUT + 1)
        self.assertEqual(h.m.cleanups, {})
        self.assertNotIn("platform", h.result())

    def test_a_missing_cleanup_unit_is_reported(self):
        h = Harness(game_card())
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]; h.pump(); h.advance(0.5)
        h.runner.cleanup = {sid: {"load": "not-found", "state": "inactive", "substate": "dead", "result": "success"}}
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertIn("su unidad no está instalada", h.result()["platform"]["message"])

    def test_outcomes(self):
        outcome = launchd.cleanup_outcome
        self.assertEqual(outcome(FakeRunner.DONE), "succeeded")
        self.assertEqual(outcome(FAILED), "failed")
        self.assertEqual(outcome(RUNNING), "pending")
        self.assertEqual(outcome({"load": "loaded", "state": "inactive", "substate": "dead", "result": "success"}), "pending",
                         "inactive is not proof of success: it is also how a cleanup looks before it starts")
        self.assertEqual(outcome({"load": "not-found"}), "missing")


class StartupCleanupSweepTests(unittest.TestCase):
    """Cleanup units left by sessions that ended while no launcher watched."""

    def test_sweep_releases_reports_and_watches(self):
        import tempfile, shutil
        root = Path(tempfile.mkdtemp()) / "launch"; root.mkdir()
        self.addCleanup(shutil.rmtree, root.parent, True)
        (root / "sorphan").mkdir()
        (root / "sorphan" / "session.json").write_text('{"card_id": "mun.collect"}')
        runner = FakeRunner()
        runner.list_game_units = lambda: []
        runner.cleanup = {
            "sorphan": dict(FAILED),                                 # its session is recorded now
            "sstored": dict(FAILED),                                 # its result was stored before the launcher died
            "sdone": dict(FakeRunner.DONE),
            "srunning": dict(RUNNING),
            "slive": {"load": "loaded", "state": "inactive", "substate": "dead", "result": "success"},
        }
        h = Harness(None)
        h.m.runner = runner
        h.m.results.store({"session": "sstored", "reason": "exited", "message": "La sesión terminó", "acknowledged": True})
        notices = launchd.sweep_cleanups(runner, h.m)
        self.assertEqual(set(notices), {"sorphan", "sstored"})
        self.assertEqual(sorted(runner.released), [("sdone", "succeeded"), ("sorphan", "failed"), ("sstored", "failed")])
        self.assertEqual(set(h.m.cleanups), {"srunning"}, "a running one is watched; an inactive one is a live game's")
        stored = []
        original = h.m.results.store
        h.m.results.store = lambda result: (stored.append(result), original(result))
        self.assertFalse(launchd.reconcile_sessions(runner, h.m, root, notices))
        orphan = next(r for r in stored if r["session"] == "sorphan")
        self.assertEqual(orphan["reason"], "interrupted")
        self.assertIn("La limpieza de la sesión falló", orphan["platform"]["message"])
        self.assertFalse((root / "sorphan").exists())


class CleanupUnitTests(unittest.TestCase):
    """N1: the cleanup is not a process of the game unit any more."""

    DEPLOY = ROOT / "services" / "mun-launchd" / "deploy"

    def test_the_game_unit_triggers_its_cleanup_unit_and_keeps_its_sandbox(self):
        content = {"device": "/dev/vdc", "root": "content"}
        props = launchd.unit_properties("linux-arm64-gl-v0", "s9", Path("/run/mun/launch/s9"), content)
        self.assertFalse([p for p in props if p.startswith("ExecStopPost")], "nothing runs in the game's mount namespace at stop")
        self.assertIn("OnSuccess=mun-launch-cleanup@s9.service", props)
        self.assertIn("OnFailure=mun-launch-cleanup@s9.service", props)
        for kept in ("MountImages=-/dev/vdc:/run/mun/card:root:ro,nosuid,nodev,noexec", "NoNewPrivileges=yes",
                     "CapabilityBoundingSet=", "ProtectSystem=strict", "PrivateNetwork=yes", "DevicePolicy=closed",
                     "DeviceAllow=/dev/vdc r", "ReadWritePaths=/run/mun/launch/s9/work", "RestrictNamespaces=yes"):
            self.assertIn(kept, props)

    def test_the_cleanup_unit_mounts_nothing_of_the_card_and_is_installed(self):
        unit = (self.DEPLOY / "mun-launch-cleanup@.service").read_text()
        self.assertIn("ExecStart=/usr/local/libexec/mun-launch-cleanup %i", unit)
        self.assertIn("Type=oneshot", unit)
        self.assertIn("RemainAfterExit=yes", unit, "success must stay visible until the launcher has seen it")
        self.assertIn("TimeoutStartSec=10s", unit)
        self.assertLess(10, launchd.CLEANUP_TIMEOUT, "the launcher waits longer than the unit's own bound")
        for absent in ("MountImages", "RootImage", "BindPaths"):
            self.assertNotIn(absent + "=", unit)
        # Installed by the staging script the image build runs.
        self.assertIn("mun-launch-cleanup@.service", (self.DEPLOY / "stage.sh").read_text())


class CardRecordsCliTests(unittest.TestCase):
    """`launchd.py cards`: the lab's authority on the current insertion's state."""

    def serve_once(self, payload):
        import socket as socketmod
        import tempfile
        import threading
        directory = tempfile.mkdtemp(prefix="npt-cards-", dir="/tmp")
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, True))
        path = Path(directory) / "cardd.sock"
        server = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
        server.bind(str(path)); server.listen(1)
        def run():
            conn, _ = server.accept()
            with conn:
                # A snapshot as cardd sends it: large, in fragments, cover included.
                for i in range(0, len(payload), 4096):
                    conn.sendall(payload[i:i + 4096])
            server.close()
        threading.Thread(target=run, daemon=True).start()
        return path

    def run_cli(self, path):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = launchd.card_records(path, timeout=5)
        return code, __import__("json").loads(out.getvalue().strip().splitlines()[-1])

    def test_snapshot_becomes_identity_and_state_only(self):
        import json
        card = {"slot": "c1", "serial": "NPT-demo", "state": "released", "insertion": "ab12", "active": True,
                "device": "vdc", "info": {"title": "Demo", "cover_data": "A" * 300000}, "error": {}}
        payload = (json.dumps({"type": "snapshot", "protocol": 1, "reader": "ready", "cards": [card]}) + "\n").encode()
        code, reply = self.run_cli(self.serve_once(payload))
        self.assertEqual(code, 0)
        self.assertEqual(reply["cards"], [{"slot": "c1", "serial": "NPT-demo", "state": "released",
                                           "insertion": "ab12", "active": True, "device": "vdc"}])
        self.assertNotIn("cover_data", json.dumps(reply), "no manifest or cover leaves the guest")

    def test_no_snapshot_or_no_service_is_an_error_not_an_empty_list(self):
        code, reply = self.run_cli(self.serve_once(b""))
        self.assertEqual(code, 1); self.assertFalse(reply["ok"]); self.assertEqual(reply["error"]["code"], "no_snapshot")
        code, reply = self.run_cli(Path("/tmp/npt-no-such-cardd.sock"))
        self.assertEqual(code, 1); self.assertEqual(reply["error"]["code"], "cardd_unavailable")


class FakeDRM:
    """A card as far as DisplayHold can tell: one connected connector on one
    CRTC showing `mode`; its master, if any, by descriptor ("shell" for the
    shell); the calls in order. As the kernel does, whoever opens the card
    while nobody is master becomes master."""

    def __init__(self, mode=None, connected=True, master="shell"):
        self.mode = mode or self.modeinfo(2560, 1440)
        self.connected, self.master, self.calls, self.fail = connected, master, [], set()
        self.crtc_fb, self.next_fd, self.closed = 40, 9, []

    @staticmethod
    def modeinfo(width, height):
        return launchd.struct.pack("<IHHHHHHHHHHIII32s", 241500, width, width + 48, width + 80, width + 160, 0,
                                   height, height + 3, height + 8, height + 41, 0, 60, 0x9, 0x40, f"{width}x{height}".encode())

    def open(self, path, flags):
        fd, self.next_fd = self.next_fd, self.next_fd + 1
        self.calls.append(f"open {fd}")
        if self.master is None:
            self.master = fd
        return fd

    def close(self, fd):
        self.closed.append(fd)
        if self.master == fd:
            self.master = None

    def __call__(self, fd, request, buffer=None, mutate=False):
        name = {v: k for k, v in vars(launchd).items() if k.startswith("DRM_") and isinstance(v, int)}[request]
        self.calls.append(name)
        if name in self.fail:
            raise OSError(22, "Invalid argument")
        if name == "DRM_DROP_MASTER":
            if self.master != fd:
                raise OSError(22, "Invalid argument")
            self.master = None
            return 0
        fmt = {"DRM_GETRESOURCES": launchd.DRM_RES, "DRM_GETCONNECTOR": launchd.DRM_CONNECTOR,
               "DRM_GETENCODER": launchd.DRM_ENCODER, "DRM_GETCRTC": launchd.DRM_CRTC, "DRM_SETCRTC": launchd.DRM_CRTC,
               "DRM_CREATE_DUMB": launchd.DRM_DUMB, "DRM_ADDFB": launchd.DRM_FB, "DRM_RMFB": "<I",
               "DRM_DESTROY_DUMB": "<I"}[name]
        values = list(launchd.struct.unpack(fmt, bytes(buffer)))
        if name == "DRM_GETRESOURCES":
            values[6] = 1
            if values[2]:
                launchd.ctypes.c_uint32.from_address(values[2]).value = 37
        elif name == "DRM_GETCONNECTOR":
            values[7], values[11] = 31, 1 if self.connected else 2
        elif name == "DRM_GETENCODER":
            values[2] = 36
        elif name == "DRM_GETCRTC":
            values[3], values[7], values[8] = self.crtc_fb, 1, self.mode
        elif name == "DRM_SETCRTC":
            if self.master != fd:
                raise OSError(13, "Permission denied")     # only the master sets a mode
            self.crtc_fb, self.mode = values[3], values[8]
        elif name == "DRM_CREATE_DUMB":
            values[4], values[5] = 7, values[1] * 4
        elif name == "DRM_ADDFB":
            values[0] = 50
        buffer[:] = launchd.struct.pack(fmt, *values)
        return 0


class DisplayHoldTests(unittest.TestCase):
    def display(self, drm):
        return launchd.DisplayHold(ioctl=drm, opener=drm.open, closer=drm.close)

    def test_the_mode_the_shell_shows_is_put_back_exactly_and_master_given_up(self):
        drm = FakeDRM()
        display = self.display(drm)
        shown = display.current()
        self.assertEqual(shown, (37, 36, drm.mode))
        self.assertEqual(drm.master, "shell", "reading takes nothing from the shell")
        drm.master = None                                            # the shell has stopped,
        drm.mode, drm.crtc_fb = FakeDRM.modeinfo(1920, 1080), 41     # and the kernel's console took the display back
        self.assertEqual(display.hold(shown), "2560x1440")
        self.assertEqual(drm.mode, shown[2], "the exact mode, as the display listed it")
        self.assertEqual(drm.crtc_fb, 50)
        self.assertIsNone(drm.master, "the game must be able to become master")
        self.assertEqual(drm.calls[-5:], ["open 10", "DRM_CREATE_DUMB", "DRM_ADDFB", "DRM_SETCRTC", "DRM_DROP_MASTER"])
        display.release()
        self.assertEqual(drm.calls[-2:], ["DRM_RMFB", "DRM_DESTROY_DUMB"])
        self.assertEqual(drm.closed, [10], "the reader stays open, the holder goes")
        self.assertIsNone(display.fb)

    def test_a_failure_never_keeps_master_or_blocks_the_game(self):
        drm = FakeDRM()
        display = self.display(drm)
        shown = display.current()
        drm.master = None
        drm.fail = {"DRM_SETCRTC"}
        self.assertIsNone(display.hold(shown))
        self.assertIsNone(drm.master)
        self.assertEqual(drm.calls[-2:], ["DRM_RMFB", "DRM_DESTROY_DUMB"], "the buffer is freed")
        without = launchd.DisplayHold(opener=lambda path, flags: (_ for _ in ()).throw(OSError(2, "No such file")))
        self.assertIsNone(without.current())

    def test_someone_else_mastering_the_display_is_left_alone(self):
        drm = FakeDRM()
        display = self.display(drm)
        shown = display.current()                # the shell has not let go
        self.assertIsNone(display.hold(shown))
        self.assertEqual(drm.master, "shell")
        self.assertEqual(drm.mode, shown[2])

    def test_nothing_is_held_without_a_connected_display(self):
        self.assertIsNone(self.display(FakeDRM(connected=False)).current())


class DisplayLaunchTests(unittest.TestCase):
    def launch(self, profile):
        card = game_card()
        card["info"]["profile"] = profile
        h = Harness(card)
        h.drm = FakeDRM()
        h.m.display = launchd.DisplayHold(ioctl=h.drm, opener=h.drm.open, closer=h.drm.close)

        def stop():
            h.shell.log.append("stop")
            h.drm.calls.append("shell stopped")
            h.drm.master = None
        h.shell.stop = stop
        reply = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})
        h.pump()
        return h, reply["session"]

    def test_a_gl_game_finds_the_consoles_mode_and_is_told_it(self):
        h, sid = self.launch(launchd.GL_PROFILE)
        calls = h.drm.calls
        self.assertLess(calls.index("DRM_GETCRTC"), calls.index("shell stopped"), "read while the shell shows it")
        self.assertLess(calls.index("shell stopped"), calls.index("DRM_SETCRTC"), "put back once the shell is gone")
        self.assertIsNone(h.drm.master, "left for the game")
        self.assertEqual(h.runner.started, [sid])
        self.assertEqual(h.m.session["display"], "2560x1440")
        env = launchd.unit_environment(launchd.GL_PROFILE, sid, h.m.session, Path("/x") / sid)
        self.assertEqual(env["MUN_DISPLAY_MODE"], "2560x1440")
        h.advance(0.5)
        h.runner.finish(sid, code=0)
        h.advance(0.5)
        self.assertIn("DRM_RMFB", h.drm.calls, "the black buffer goes with the session")
        self.assertEqual(h.shell.log, ["stop", "start"])

    def test_a_framebuffer_game_is_left_the_kernels_console(self):
        h, sid = self.launch(launchd.FRAMEBUFFER_PROFILE)
        self.assertNotIn("DRM_SETCRTC", h.drm.calls)
        self.assertNotIn("display", h.m.session)
        self.assertEqual(h.runner.started, [sid])
