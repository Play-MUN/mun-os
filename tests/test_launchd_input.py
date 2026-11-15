"""What mun-launchd does with bytes it did not write: request lines from a game
and from the shell, the save envelope of a card and the save units of a game.
Whatever it cannot handle is a refusal of that input, never an exception in
the launcher."""

import contextlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import tracemalloc
import unittest
import unittest.mock
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mun-launchd"))
sys.path.insert(0, str(ROOT / "tests"))

import launchd  # noqa: E402
import test_directory_saves as directory_saves  # noqa: E402
from test_directory_saves import SLOT, envelope  # noqa: E402
from test_launchd import Harness, game_card  # noqa: E402

SAVE = b'{"type":"save","schema":1,"data":{"level":3}}\n'


def nested(depth: int, opener: bytes = b"[", closer: bytes = b"]", core: bytes = b"1") -> bytes:
    return opener * depth + core + closer * depth


class RequestLineTests(unittest.TestCase):
    def reader(self):
        return launchd.LineReader(limit=launchd.SAVE_LINE_MAX)

    def test_a_line_nested_beyond_the_bound_is_refused_and_the_next_one_is_read(self):
        for hostile in (b"[" * 20000, nested(20000), nested(990, b'{"a":', b"}"),
                        b'{"type":"save","schema":1,"data":{"a":' + b"[" * 60000):
            with self.subTest(hostile[:12]):
                self.assertEqual(self.reader().feed(hostile + b"\n" + SAVE), [json.loads(SAVE)])

    def test_the_bound_is_a_number_of_levels_not_of_brackets(self):
        limit = launchd.JSON_MAX_DEPTH
        self.assertEqual(len(self.reader().feed(nested(limit - 1, b'{"a":', b"}") + b"\n")), 1)
        self.assertEqual(self.reader().feed(b'{"a":' + nested(limit) + b"}\n"), [], "one level too many")
        wide = b'{"rows":[' + b",".join(b'{"x":[1,2]}' for _ in range(2000)) + b"]}\n"
        self.assertEqual(len(self.reader().feed(wide)), 1, "many siblings are not depth")
        text = json.dumps({"note": "[" * 5000 + '\\"{{{' + "{" * 5000}).encode() + b"\n"
        self.assertEqual(len(self.reader().feed(text)), 1, "brackets inside a string are text")

    def test_a_finished_line_over_the_limit_is_a_protocol_break_like_an_unfinished_one(self):
        reader = launchd.LineReader(limit=64)
        with self.assertRaises(launchd.FrameTooLarge):
            reader.feed(b'{"pad":"' + b"x" * 100 + b'"}\n')
        self.assertEqual(reader.feed(b'{"ok":1}\n'), [{"ok": 1}], "nothing of the refused line is kept")

    def test_a_deep_save_inside_the_bound_is_still_forwarded(self):
        h = Harness(game_card())
        saves = []
        h.m.saver = lambda request, reply: saves.append(request)
        sid = h.m.launch({"slot": "c1", "serial": "NPT-game", "version": "0.1.0"})["session"]
        h.pump()
        h.advance(0.5)
        line = b'{"type":"save","schema":1,"data":' + nested(launchd.JSON_MAX_DEPTH - 2, b'{"a":', b"}") + b"}\n"
        (request,) = self.reader().feed(line)
        replies = []
        h.m.save_request(sid, request, replies.append)
        self.assertEqual((replies, [s["payload"] for s in saves]), ([], [request["data"]]),
                         "accepted and sent to the card service")


class FakeManager:
    state = "idle"

    def snapshot(self):
        return {"type": "snapshot", "state": "idle"}


class ShellSocketTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="ml"))
        self.addCleanup(shutil.rmtree, folder, True)
        self.server = launchd.Server(folder / "l.sock", FakeManager())
        with contextlib.redirect_stdout(io.StringIO()):      # no mun-shell group on a host
            listener = self.server.start()
        self.addCleanup(listener.close)
        self.shell = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.shell.close)
        self.shell.settimeout(2)
        self.shell.connect(str(folder / "l.sock"))
        self.conn = self.server.accept()
        self.addCleanup(self.conn.close)
        self.assertEqual(self.read()["type"], "snapshot")

    def read(self) -> dict:
        data = b""
        while not data.endswith(b"\n"):
            data += self.shell.recv(65536)
        return json.loads(data)

    def test_lines_that_are_not_a_request_object_are_ignored_and_the_client_is_kept(self):
        for line in (b"1\n", b"[]\n", b'"x"\n', b"null\n", b"[" * 4000 + b"\n", b"not json\n"):
            with self.subTest(line[:12]):
                self.shell.sendall(line)
                self.assertTrue(self.server.handle(self.conn))
        self.shell.sendall(b'{"type":"status"}\n')
        self.assertTrue(self.server.handle(self.conn))
        self.assertEqual(self.read(), {"type": "snapshot", "state": "idle"})

    def test_a_client_that_never_ends_its_line_is_dropped_with_nothing_kept(self):
        sent, alive = 0, True
        while alive and sent < 4 * 1024 * 1024:
            self.shell.sendall(b"A" * 2048)
            sent += 2048
            alive = self.server.handle(self.conn)
        self.assertFalse(alive, "kept reading a line with no end")
        self.assertLessEqual(sent, launchd.CLIENT_LINE_MAX + 65536 + 2048)
        self.assertNotIn(self.conn, self.server.clients)


class CardEnvelopeTests(directory_saves.SyncCase):
    def test_an_envelope_nested_beyond_the_bound_is_a_damaged_save_not_a_failure_of_the_launch(self):
        for hostile in (b"[" * 70000, nested(70000), b'{"format":' + nested(5000, b'{"a":', b"}") + b"}"):
            with self.subTest(hostile[:12]):
                self.setUp()
                self.prepare(card_save=hostile)
                self.assertEqual(self.sync.restore, {"state": "damaged", "detail": "no es JSON"})
                self.assertEqual(list(self.savedir.iterdir()), [])

    def test_an_ordinary_envelope_is_restored(self):
        self.prepare(card_save=envelope({"save-0000.sav": SLOT}))
        self.assertEqual(self.sync.restore["state"], "restored")
        self.assertEqual((self.savedir / "save-0000.sav").read_bytes(), SLOT)


def xml_unit(encoding: str, body: bytes = b'<Flag a="1"/>') -> bytes:
    return b'<?xml version="1.0" encoding="' + encoding.encode("ascii") + b'"?>\n' + body + b"\n"


# Encodings the XML parser cannot read: a name it does not know, multibyte
# ones (a Japanese engine's own), and codecs that are not character sets.
UNREADABLE = ("x-nope", "shift_jis", "euc-jp", "gb2312", "utf-7", "idna", "zlib")
JAPANESE = '<?xml version="1.0" encoding="Shift_JIS"?>\n<セーブ 名前="テスト"/>\n'.encode("shift_jis")


class SaveUnitEncodingTests(unittest.TestCase):
    """A unit in an encoding the console cannot read fails its check (docs/saves.md)."""

    def test_an_unreadable_encoding_fails_the_check_with_its_reason(self):
        for check, wrap in (("xml", bytes), ("zlib-xml", zlib.compress)):
            for encoding in UNREADABLE:
                with self.subTest(check=check, encoding=encoding):
                    self.assertIn("codificación", launchd.unit_check(check, wrap(xml_unit(encoding))))
            with self.subTest(check=check, encoding="a real Shift-JIS document"):
                self.assertIn("codificación", launchd.unit_check(check, wrap(JAPANESE)))

    def test_the_encodings_the_parser_reads_still_pass(self):
        accented = '<Nombre jugador="Iván Peña"/>'
        for encoding, codec in (("UTF-8", "utf-8"), ("utf-8", "utf-8"), ("US-ASCII", "ascii"), ("ISO-8859-1", "latin-1"),
                                ("windows-1252", "cp1252")):
            body = (accented if codec != "ascii" else '<Nombre jugador="Ivan"/>').encode(codec)
            with self.subTest(encoding=encoding):
                self.assertIsNone(launchd.unit_check("xml", xml_unit(encoding, body)))
                self.assertIsNone(launchd.unit_check("zlib-xml", zlib.compress(xml_unit(encoding, body) + b"\0")))
        self.assertIsNotNone(launchd.unit_check("xml", xml_unit("UTF-8", accented.encode("latin-1"))),
                             "bytes that are not the declared encoding are still a damaged unit")


class SaveUnitSessionTests(unittest.TestCase):
    """The same unit met in a session: written by the game, or held by the card."""

    BAD = zlib.compress(xml_unit("x-nope"))
    # The directory-save session fixture.
    start, write = directory_saves.SessionTests.start, directory_saves.SessionTests.write

    def test_a_running_game_writes_one_and_the_session_goes_on(self):
        h, sid = self.start()
        self.write(h, "save-0000.sav", self.BAD)
        h.advance(0.5)
        self.assertEqual((h.m.state, h.saves), ("running", []), "nothing to write: the only unit is not complete")
        self.write(h, "save-0001.sav", SLOT)
        h.advance(0.5)
        self.assertEqual(h.saves[-1][0]["payload_kind"], "files", "a good unit beside it is still saved")
        h.saves[-1][1]({"ok": True, "bytes": 900})
        h.pump()
        h.runner.finish(sid, code=0)
        h.advance(0.5)
        h.pump()
        self.assertEqual(h.m.state, "idle")
        saves = h.result()["saves"]
        self.assertEqual((saves["ok"], saves["omitted"]), (False, ["save-0000.sav"]))
        self.assertIn("Una ranura nueva estaba incompleta y no se guardó", saves["message"])

    def test_the_game_writes_one_and_exits(self):
        h, sid = self.start()
        self.write(h, "save-0000.sav", self.BAD)
        h.runner.finish(sid, code=0)
        h.advance(0.5)
        h.pump()
        self.assertEqual((h.m.state, h.result()["reason"]), ("idle", "exited"))
        self.assertEqual(h.result()["saves"]["omitted"], ["save-0000.sav"])

    def test_play_with_one_in_the_cards_save_starts_the_game_without_it(self):
        h, sid = self.start(envelope({"save-0000.sav": self.BAD}))
        self.assertEqual(h.m.sync.restore["state"], "damaged")
        self.assertIn("codificación", h.m.sync.restore["detail"])
        self.assertEqual(list(h.savedir.iterdir()), [])

    def test_a_unit_in_a_readable_encoding_is_saved_and_restored(self):
        latin = zlib.compress(xml_unit("ISO-8859-1", '<Nombre jugador="Iván"/>'.encode("latin-1")))
        h, sid = self.start(envelope({"save-0000.sav": latin}))
        self.assertEqual((h.savedir / "save-0000.sav").read_bytes(), latin)
        self.write(h, "save-0001.sav", latin)
        h.advance(0.5)
        self.assertEqual((h.saves[-1][0]["payload_kind"], h.m.sync.omitted), ("files", []))


MIB = 1024 * 1024


class SaveUnitCostTests(directory_saves.SyncCase):
    """What checking one unit may cost the launcher, whatever the unit holds."""

    MARKUP = b"<a b='1'/>"

    def checked(self, check: str, unit: bytes, *limit) -> "tuple":
        tracemalloc.start()
        started = time.monotonic()
        try:
            problem = launchd.unit_check(check, unit, *limit)
            return problem, tracemalloc.get_traced_memory()[1], time.monotonic() - started
        finally:
            tracemalloc.stop()

    def test_a_small_unit_that_inflates_to_64_mib_of_markup_is_refused_cheaply(self):
        unit = zlib.compress(self.MARKUP * ((64 * MIB - 64) // len(self.MARKUP)), 9)
        self.assertLess(len(unit), 160 * 1024, "inside every bound a card may declare")
        problem, peak, seconds = self.checked("zlib-xml", unit)
        self.assertIn("límite", problem or "accepted")
        self.assertLess(peak, 64 * MIB, f"peak {peak / MIB:.0f} MiB in {seconds:.1f} s")

    def test_the_largest_xml_a_unit_may_hold_is_checked_without_building_a_document(self):
        body = self.MARKUP * (launchd.XML_INFLATE_LIMIT // len(self.MARKUP))
        plain = self.MARKUP * (8 * MIB // len(self.MARKUP))        # the most a card may declare
        for check, unit in (("zlib-xml", zlib.compress(body, 1)), ("xml", plain)):
            with self.subTest(check):
                problem, peak, seconds = self.checked(check, unit)
                self.assertIsNone(problem)
                self.assertLess(peak, 3 * launchd.XML_INFLATE_LIMIT, f"peak {peak / MIB:.0f} MiB in {seconds:.1f} s")
        self.assertIn("XML incompleto", launchd.unit_check("zlib-xml", zlib.compress(plain + b"<a><b></a>", 1)),
                      "and what it holds is still read to its end")
        self.assertIn("límite", launchd.unit_check("zlib-xml", zlib.compress(body + self.MARKUP, 1)))

    def test_saves_of_the_size_real_engines_write_still_pass(self):
        rows = "".join(f'<Entity id="{n}" x="{n * 3.5:.2f}" y="{n * 1.25:.2f}" state="idle"><Flag name="seen{n}" v="1"/>'
                       f"<Note>caf\u00e9 &amp; t\u00e9 {n}</Note></Entity>\n" for n in range(4000)).encode("utf-8")
        save = b"<?xml version='1.0' encoding='UTF-8'?>\n<Version major='1'/>\n<World>" + rows + b"</World>\n\0"
        self.assertGreater(len(save), 400 * 1024)
        self.assertIsNone(launchd.unit_check("zlib-xml", zlib.compress(save)))
        self.assertIsNone(launchd.unit_check("xml", save))
        thumbnail = zlib.compress(bytes(4 * MIB + 18))            # a 1024x1024 RGBA picture, all black
        self.assertLess(len(thumbnail), 8192)
        self.assertIsNone(launchd.unit_check("zlib", thumbnail))

    def test_a_unit_may_inflate_only_so_far_beyond_what_the_card_declared_for_its_saves(self):
        self.assertEqual(launchd.inflate_limit(8 * MIB), launchd.INFLATE_LIMIT, "the absolute bound")
        small = launchd.inflate_limit(4096)
        self.assertLess(small, MIB)
        self.assertIsNone(launchd.unit_check("zlib", zlib.compress(bytes(small)), small))
        self.assertIn("límite", launchd.unit_check("zlib", zlib.compress(bytes(small + 1)), small))
        # In a session: the unit is not complete, so it is left out and reported.
        self.sync = launchd.DirectorySync("s1", dict(directory_saves.SPEC, max_bytes=4096), self.dest, os.getuid(),
                                          os.getgid(), self.host, lambda *sent: self.sent.append(sent),
                                          wall=lambda: self.clock[0], inotify_factory=lambda: self.watch,
                                          root_ids=(os.getuid(), os.getgid()), lease=self.host.lease)
        self.prepare()
        self.game_writes("screen-0000.zsc", zlib.compress(bytes(small + 1)))
        self.sync.poll()
        self.assertEqual((self.sent, self.sync.omitted), ([], ["screen-0000.zsc"]))


if __name__ == "__main__":
    unittest.main()


def traced(work):
    """(result, seconds, peak bytes Python allocated) of work()."""
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = work()
        return result, time.perf_counter() - started, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


class EnvelopeCostTests(directory_saves.SyncCase):
    """What a card's save envelope may cost the launcher, which reads it as
    root before the game starts: a card declaring the largest directory saves
    (8 MiB) may hand it 16 MiB, and that much JSON made of tiny values parses
    into hundreds of megabytes of objects."""

    def setUp(self):
        super().setUp()
        self.sync.max_bytes = 8 * 1024 * 1024
        self.sync.inflate_limit = launchd.inflate_limit(self.sync.max_bytes)

    def test_an_envelope_of_countless_small_values_is_refused_cheaply(self):
        size = self.sync.max_bytes * 2 + 128 * 1024
        for hostile in (b"[" + b"0," * (size // 2) + b"0]",
                        b'{"format":"neptune-save/1","x":{' + b'"a":0,' * (size // 6) + b'"a":0}}'):
            with self.subTest(hostile[:14]):
                self.setUp()
                _, seconds, peak = traced(lambda: self.prepare(card_save=hostile))
                self.assertEqual(self.sync.restore["state"], "damaged")
                self.assertLess(peak, 64 * 1024 * 1024, "parsed into objects")
                self.assertLess(seconds, 1.0)

    def test_the_largest_envelope_a_card_may_hold_is_still_restored(self):
        # 64 units, 8 MiB in all: the most a card declaring 8 MiB may store.
        unit = zlib.compress(os.urandom(8 * 1024 * 1024 // 64 - 64), 0)
        files = {f"save-{n:04d}.sav": zlib.compress(b"<Flag n='%d'/>" % n) + b"" for n in range(1)}
        files.update({f"screen-{n:04d}.zsc": unit for n in range(63)})
        _, seconds, peak = traced(lambda: self.prepare(card_save=directory_saves.envelope(files)))
        self.assertEqual(self.sync.restore, {"state": "restored", "units": 64})
        self.assertLess(peak, 192 * 1024 * 1024)

    def test_many_small_inflation_bombs_cost_a_few_units_not_one_each(self):
        # Each unit inflates to its own limit (64 MiB): 64 of them used to make
        # the check inflate 4 GiB before the game started.
        bomb = zlib.compress(b"\0" * (self.sync.inflate_limit - 1024), 9)
        files = {f"screen-{n:04d}.zsc": bomb for n in range(64)}
        _, seconds, _ = traced(lambda: self.prepare(card_save=directory_saves.envelope(files)))
        self.assertEqual(self.sync.restore["state"], "damaged")
        self.assertIn("límite", self.sync.restore["detail"])
        self.assertLess(seconds, 4.0)
        self.assertEqual(launchd.inflate_share(self.sync.inflate_limit, 64), self.sync.inflate_limit * 4 // 64)
        self.assertEqual(launchd.inflate_share(self.sync.inflate_limit, 2), self.sync.inflate_limit)


class ForwardedSaveTests(unittest.TestCase):
    """A save the launcher accepts (at most 64 KiB as compact UTF-8) must
    reach the card service in a line that service takes: escaped, every
    character outside ASCII became six bytes and a save well inside the
    bound was refused as a request too large."""

    def sent_line(self, data):
        sent, replies = [], []

        class Socket:
            def __init__(self, *args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def settimeout(self, value):
                pass

            def connect(self, path):
                pass

            def sendall(self, line):
                sent.append(line)

            def recv(self, size):
                return b'{"ok": true}\n'

        request = {"type": "save", "session": "s1", "schema": 1, "data": data}
        with unittest.mock.patch.object(launchd.socket, "socket", Socket):
            launchd.stage_via_cardd(request, replies.append)
            deadline = time.monotonic() + 5
            while not replies and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(replies, [{"ok": True}])
        return sent[0]

    def test_a_save_of_text_outside_ascii_reaches_the_card_service_within_its_line(self):
        sys.path.insert(0, str(ROOT / "tools" / "mun-card"))
        from mun_card.saves import SAVE_FILE_MAX_BYTES
        data = {"name": "é" * 32000}
        compact = len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(compact, launchd.SAVE_MAX_BYTES, "a save the launcher accepts")
        line = self.sent_line(data)
        self.assertLessEqual(len(line), SAVE_FILE_MAX_BYTES)
        self.assertEqual(json.loads(line)["data"], data)

    def test_an_ascii_save_is_forwarded_as_before(self):
        self.assertEqual(json.loads(self.sent_line({"n": 1}))["data"], {"n": 1})
