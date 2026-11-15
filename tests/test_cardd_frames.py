"""What mun-cardd sends and logs is always valid UTF-8, whatever a card or a
request put in a string. JSON can spell a code point no UTF-8 text can hold
(a lone surrogate, as the escape `\\ud800`), so a card's file or a game's
request can bring one in as plain ASCII; it must never end the service."""

import io
import json
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

import cardd  # noqa: E402
from test_cardd import FakeMounter, game_info  # noqa: E402

LONE = "\ud800"     # what json.loads makes of the six ASCII characters \ud800


class FrameEncodingTests(unittest.TestCase):
    def test_a_frame_with_a_lone_surrogate_is_sent_as_valid_utf8_json(self):
        message = {"type": "card", "card": {"slot": "c1", "state": "valid", "info": {"title": "A" + LONE + "B"}}}
        frame = cardd.encode_frame(message)
        self.assertTrue(frame.endswith(b"\n"))
        decoded = json.loads(frame.decode("utf-8"))            # strict: what every consumer does
        self.assertEqual(decoded["card"]["state"], "valid")
        self.assertEqual(decoded["card"]["info"]["title"], "A?B")

    def test_keys_and_nested_values_are_covered_too(self):
        frame = cardd.encode_frame({"type": "shape", LONE: [{"detail": LONE + "\udfff"}]})
        self.assertEqual(json.loads(frame.decode("utf-8")), {"type": "shape", "?": [{"detail": "??"}]})

    def test_ordinary_text_is_sent_unchanged(self):
        message = {"type": "card", "card": {"info": {"title": "Colección ñ — 収集 🙂"}}}
        self.assertEqual(json.loads(cardd.encode_frame(message).decode("utf-8")), message)
        self.assertIn("Colección ñ — 収集 🙂".encode("utf-8"), cardd.encode_frame(message))

    def test_an_oversized_frame_with_a_lone_surrogate_still_loses_only_its_cover(self):
        message = {"type": "card", "card": {"state": "valid", "info": {"title": LONE, "cover_data": "x" * cardd.MAX_FRAME_BYTES}}}
        with redirect_stdout(io.StringIO()):
            frame = cardd.encode_frame(message)
        info = json.loads(frame.decode("utf-8"))["card"]["info"]
        self.assertEqual((info["title"], info.get("cover_omitted")), ("?", True))

    def test_a_log_line_with_a_lone_surrogate_is_written_not_raised(self):
        # The service's stdout is the journal; as strict as Python makes it.
        raw = io.BytesIO()
        strict = io.TextIOWrapper(raw, encoding="utf-8", errors="strict")
        with redirect_stdout(strict):
            cardd.log(f"save for session s{LONE}: failed")
            cardd.log("una línea corriente — ñ")
        strict.flush()
        self.assertEqual(raw.getvalue().decode("utf-8"), "save for session s?: failed\nuna línea corriente — ñ\n")


class ShapePackageMounter(FakeMounter):
    """Mounts a real directory holding a game card whose content carries one
    file, `content/mun-shape/shape.json`, with the bytes given."""

    def __init__(self, root, shape_json):
        super().__init__()
        self.root, self.shape_json = root, shape_json

    def mount(self, card, path):
        target = self.root / "cards" / card.slot
        (target / "content" / "mun-shape").mkdir(parents=True)
        (target / "saves").mkdir()
        (target / "content" / "mun-shape" / "shape.json").write_bytes(self.shape_json)
        self.mounted[card.slot] = str(target)
        return str(target)


class HostileShapeDocumentTests(unittest.TestCase):
    """A card whose Shape document is nineteen bytes of ASCII, through the
    real manager, export worker and event socket."""

    def insert(self, shape_json):
        tmp = Path(tempfile.mkdtemp(prefix="cardd-frames-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        server = cardd.Server(Path("/nonexistent.sock"), "nobody")
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close); self.addCleanup(theirs.close)
        ours.setblocking(False)
        server.clients.append(ours)
        pending = []
        manager = cardd.CardManager(ShapePackageMounter(tmp, shape_json), lambda mount: game_info(),
                                    server.broadcast, pending.append, shape_root=tmp / "shape",
                                    later=lambda seconds, fn: None, remove_export=cardd._remove_tree)
        server.manager = manager
        with redirect_stdout(io.StringIO()):
            manager.device_added("vdc", "NPT-game", "/dev/vdc")
            waited = threading.Event()
            for _ in range(1000):
                while pending:
                    pending.pop(0)()            # the loop's queue: nothing here may raise
                card = next(iter(manager.cards.values()))
                if card.shape and card.shape.get("state") != "preparing":
                    break
                waited.wait(0.01)
            snapshot = cardd.encode_frame(manager.snapshot())    # what a client that connects now is sent
        theirs.settimeout(5)
        received = b""
        while True:
            received += theirs.recv(1 << 16)
            if not received.endswith(b"\n"):
                continue
            lines = [json.loads(line.decode("utf-8")) for line in received.splitlines()]
            if lines[-1]["type"] == "shape" and lines[-1]["shape"]["state"] != "preparing":
                return manager, lines, json.loads(snapshot.decode("utf-8"))

    def test_a_shape_document_with_a_lone_surrogate_leaves_the_card_valid_and_the_service_running(self):
        manager, lines, snapshot = self.insert(b'{"format":"\\ud800"}')
        card = next(iter(manager.cards.values()))
        self.assertEqual(card.state, "valid")
        self.assertEqual(card.shape["state"], "unused")
        shapes = [line for line in lines if line["type"] == "shape"]
        self.assertEqual(shapes[-1]["shape"]["state"], "unused")
        self.assertTrue(shapes[-1]["shape"]["notes"], "the note that quotes the card's value arrived")
        self.assertEqual(snapshot["cards"][0]["state"], "valid")
        self.assertEqual(snapshot["cards"][0]["shape"]["state"], "unused")

    def test_an_ordinary_unknown_format_is_reported_the_same_way(self):
        manager, lines, snapshot = self.insert(b'{"format":"nope"}')
        card = next(iter(manager.cards.values()))
        self.assertEqual((card.state, card.shape["state"]), ("valid", "unused"))
        shapes = [line for line in lines if line["type"] == "shape"]
        self.assertIn("nope", json.dumps(shapes[-1]["shape"]["notes"]))


if __name__ == "__main__":
    unittest.main()
