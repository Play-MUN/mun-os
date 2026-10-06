# MUN Collect

**English** · [Español](README.es.md)

A small test game that lives on a Game Card: move the ink square with the
arrow keys, collect the five copper discs, press S to save your progress on
the card, Esc to return to the console. Its purpose is to prove the launch
path and the save path, not to be fun.

- C11, no libraries: framebuffer output (`fb.c`) and evdev input (`input.c`),
  rules in `game.c`, drawing and an embedded 5×7 font in `draw.c`.
- Queries the framebuffer mode before drawing (resolution, 32 bpp true colour,
  line length) and refuses anything else; finds keyboards by capability
  (arrows + Esc), never by device number.
- Exits 0 on Esc; 2 if the framebuffer is unusable; 3 if no keyboard is found.
  SIGTERM ends the loop cleanly and restores a blank screen.
- Reads `MUN_CARD_ID` (informational) and `MUN_FRAMEBUFFER` (defaults to
  `/dev/fb0`). Every console variable is read under its `MUN_` name first and
  its earlier `NEPTUNE_` name second, so the same source runs on consoles
  from before the MUN naming; earlier binaries read only `NEPTUNE_`, which
  the console still publishes for cards of the earlier generation
  ([naming generations](../../docs/game-cards.md#naming-generations)). It
  accepts both save envelopes, `mun-save/1` and `neptune-save/1`; the
  console hands a card only its own. No network.
- Saves through the console, never to the card itself ([saves](../../docs/saves.md#single-object-saves)). At start it
  reads `MUN_SAVE_FILE` (the current save the console copied into its
  working directory): a compatible save restores the seed, so the discs are
  where they were, the position, the collected discs and the elapsed time;
  no file means a new game; a copy the console recovered from
  `save.json.prev` is announced as "COPIA ANTERIOR: n/5"; a damaged or
  incompatible file is announced on the HUD and is not overwritten unless the
  player saves on purpose. **S** asks
  for a save over `MUN_SAVE_SOCKET`: the HUD shows "GUARDANDO..." while
  the console writes and only then "GUARDADO EN LA GAME CARD", or "ERROR AL
  GUARDAR: <cause>" (`NO_SPACE`, `CARD_REMOVED`, `SAVE_BUSY`, …). Without
  either variable the game plays but cannot save.
- Save payload (schema 1, `save.c`): `{"seed","x","y","elapsed","collected",
  "taken":[0|1 ×5]}`, at most 64 KiB by contract, a few hundred bytes in
  practice. The console wraps it in its envelope; the game validates the
  envelope's `format` and `schema` and the payload's internal consistency
  (`collected` must equal the number of `taken` flags) before trusting it.

The image build compiles it (gcc 14, static) against the image's own
toolchain and leaves it beside the image, never in it:

```sh
./mun dev build --name one
python3 tools/mun-card/mun-card create game --variant game --game .local/mun/builds/one/games/mun-collect/mun-collect
```

The build records the binary's size and SHA-256 and the toolchain in
`BUILD-INFO.json`; it is not a verified reproducible build. Any Debian 13
arm64 system with gcc and make can also build it with `make`.
`make test` (any host C compiler) runs `tests/test_save.c`: round trip,
truncated, non-JSON, wrong format, other schema, inconsistent payload, deep
nesting; the top-level `make check` runs it when a compiler is present.
