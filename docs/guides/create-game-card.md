# Create a Game Card

Make your own Game Card with MUN Collect, the example game: give it an
identity and a cover, check it, play it in the console, save on it, eject
it, and continue from it after the console restarts. Twenty minutes, no
compiler required.

A Game Card is an ext4 disk image here; on the console it will be a physical
card. It carries a manifest (`mun.toml`), the game under `content/`, an
optional cover and a `saves/` directory the console writes the player's
progress to. The rules are in [Game Cards](../game-cards.md) and
[saves](../saves.md).

## Before you start

- The tools and a preview image, as in [getting started](../getting-started.md)
  (sections 1 to 3): you will have `.local/gamecards/collect.img`.
- e2fsprogs 1.47 or later, which the card tool uses to write and read card
  images without mounting them: `brew install e2fsprogs` on macOS, the
  `e2fsprogs` package on Linux. Windows: use WSL 2
  ([compatibility](../compatibility.md#making-game-cards)).

## 1. The game: an ARM64 Linux program

The console runs AArch64 Linux programs. MUN Collect is one: C, no libraries,
statically linked, drawing on the framebuffer. Take it from the card you
downloaded, read-only (`debugfs` comes with e2fsprogs; on macOS it is in
`$(brew --prefix e2fsprogs)/sbin/`):

```sh
debugfs -R "dump /content/mun-collect mun-collect" .local/gamecards/collect.img
chmod +x mun-collect
file mun-collect    # ELF 64-bit LSB executable, ARM aarch64, … statically linked
```

Or compile it: an image build leaves it in
`.local/mun/builds/<build>/games/mun-collect/mun-collect`, and any Debian 13
arm64 system with gcc and make builds it with `make -C examples/mun-collect`
([the example](../../examples/mun-collect/README.md)).

## 2. Its identity and look

Choose:

| | Option | Example |
| --- | --- | --- |
| Title the console shows | `--title` | `"My Collect"` |
| Identifier, the same for every edition of the game; 3–64 lowercase letters, digits, `.`, `-`, `_` | `--id` | `org.example.mycollect` |
| Content version | `--version` | `1.0.0` |
| Cover: a PNG of at most 1 MiB and 1024×1024 | `--cover` | `cover.png` |
| Colours the console takes while the card is in: the focus, and the light at the centre | `--accent`, `--background` | `"#2E7EC5"`, `"#BFD9F2"` |

The identifier is what saves belong to: keep it when you make a new version
of the same game, change it for a different game. The cover and colours are
optional; without a cover the tool draws one.

## 3. Make the card

```sh
./mun card create mycollect --variant game --game mun-collect \
    --title "My Collect" --id org.example.mycollect --version 1.0.0 \
    --accent "#2E7EC5" --background "#BFD9F2"
```

`--variant game` is a game for the framebuffer profile, as MUN Collect is;
`game-gl` is for games built on SDL2, OpenGL and OpenAL, with `--content` for
their data ([bring a game to MUN](port-a-game.md)). The card is written to
`.local/gamecards/mycollect.img` (64 MiB; `--size` changes it), with a few
small sample files of the tool's beside the game in `content/`. An identifier
or version the console would refuse is refused here, and an existing card is
never overwritten without `--force`.

## 4. Check it

```sh
./mun card inspect mycollect
```

```text
.local/gamecards/mycollect.img
  VÁLIDA  My Collect (org.example.mycollect) v1.0.0 · game · aarch64/linux-arm64-v0 · portada: cover.png · declara ejecutable content/mun-collect
  nombres: MUN (mun.toml, partidas mun-save/1)
  sha256 … · sin cambios tras inspección: sí
```

`inspect` runs the console's own validator on the image without mounting or
changing it (the card tools speak Spanish, as the console's services do). A
card it refuses, the console refuses too, with the same reason.
`./mun card hash mycollect --files` lists every file on the card with its
SHA-256.

## 5. Play and save

```sh
./mun play --card mycollect
```

The console starts in a window and the card goes in: the Game Card entry says
"My Collect", in your accent colour. **Enter, Enter** plays. Move the square
with the arrows and collect a disc or two, then press **S**: "GUARDADO EN LA
GAME CARD". The console has written your progress to the card's `saves/`,
atomically, keeping the previous copy. **Esc** ends the game; **Enter** for
OK on "Session ended".

## 6. Eject, restart, continue

On the card's panel choose **Eject safely**: the console finishes with the
card, unmounts it and the card leaves the slot ("You can remove the Game
Card"). Then **Turn off** (Up from Game Card, Enter, Enter): the console
shuts down and the window closes.

Start it again, with the card:

```sh
./mun play --card mycollect
```

Enter, Enter: "PARTIDA RECUPERADA", the square where you saved it. The
progress was on the card, not in the console; another console, or an image
built another day, continues from it the same way.

`./mun card hash mycollect --files --ignore saves` gives the same digests as
before you played: the game and its content never change, only `saves/`.

## Packaging is not porting

A Game Card holds a program the console can run: an AArch64 Linux executable
for one of the console's runtime profiles, with the libraries that profile
provides. Putting a Windows or macOS program, or an x86_64 Linux one, on a
card does not make it a MUN game: the card is valid, and the game fails to
start. Bringing a game to MUN means building it for ARM64 Linux against the
console's libraries, and making it save where the console keeps saves:
[bring a game to MUN](port-a-game.md).

## Going further

- Deliberately broken cards, to see how the console refuses them:
  `./mun card variants`, then `./mun card create broken --variant bad-arch`.
- Two consoles, one card: a card is only ever in one running console at a
  time; the laboratory's commands insert and remove cards from a script
  ([laboratory](../../vm/README.md#game-cards)).
- The contracts: [Game Cards](../game-cards.md), [saves](../saves.md),
  [running a game](../runtime.md).
