# Where to change what

**English** · [Español](es/contributor-map.md)

A short map for a first change: where each part of MUN OS lives, and how to
check it. [CONTRIBUTING](../CONTRIBUTING.md) says how changes are proposed;
[architecture](architecture.md) says how the parts fit.

## The map

| To change | Look in | Check with |
| --- | --- | --- |
| What a panel or dialog says, its options, the menus | `services/mun-shell/qml/Panels.qml` (texts and options), `qml/Main.qml` (navigation, keys, dialogs), `qml/I18n.qml` (the two languages) | the image build, which runs the shell's behaviour regressions; screenshots in a laboratory console |
| A setting | `services/mun-shell/src/shellsettings.*` (values, file), `qml/Panels.qml` (its section), the settings table in the [shell's README](../services/mun-shell/README.md#settings) | the `settings` and `home` behaviour cases |
| What Home draws (the network of light, the objects, the start-up) | `services/mun-shell/src/` (`backdrop.*`, `heroicon.*`, `bootintro.*`) | behaviour regressions; frames and timings in a laboratory console |
| The Game Card format, `mun-card` | `tools/mun-card/mun_card/`, [Game Cards](game-cards.md) | `tests/test_card.py` |
| Reading cards, saves | `services/mun-cardd/cardd.py`, [saves](saves.md) | `tests/test_cardd.py`, `tests/test_directory_saves.py` |
| Starting and ending a game | `services/mun-launchd/launchd.py`, [runtime](runtime.md) | `tests/test_launchd.py`, `tests/test_launch_cleanup.py` |
| MUN Shape | [MUN Shape](shape.md), `tools/mun-card/mun_card/shape*.py`, `services/mun-shell/src/shape*`, `examples/shape/` | `tests/test_shape.py`, `tests/test_cardd_shape.py`, the `world` and `memory` behaviour cases |
| Resetting the console | `services/mun-reset/` | `tests/test_reset.py` |
| What the image contains | `os/mkosi/` (packages, files, profiles), `os/inputs.json` (pinned inputs) | `tests/test_os.py`, `tests/test_deploy.py`, then `./mun dev build` |
| The laboratory (`./mun dev`, `./mun vm`) | `vm/`, `mun` | `tests/test_vm.py`, `tests/test_mundev.py` |
| The example games | `examples/` | their own tests (`examples/mun-collect/tests`), a card made with `mun-card` |
| Documentation and its translations | `docs/`, `docs/es/`, the `*.es.md` beside an original, `docs/translations.json` | `make check` |

## The checks

- **`make check`**, on Linux and macOS: the documentation (links, anchors,
  translations reviewed against their originals), Python syntax and the
  host tests in `tests/` ([what each covers](../tests/README.md)). It needs
  no QEMU, no root and no network.
- **`./mun dev build`**: builds an image in a disposable builder and runs the
  shell's behaviour regressions on the compiled binary (the shell needs Qt,
  which the host checks do not have).
- **A laboratory console**: `./mun dev run` boots a build, `./mun vm
  screenshot` takes what is on screen and `./mun dev smoke` plays a card from
  start to end ([laboratory](../vm/README.md)).

## Three sizes, not to be confused

| What | About | Where |
| --- | --- | --- |
| The repository | 8 MiB of files, history included in a few more | what Git holds |
| The installed system | 700 MiB on the console's root file system; a download of about 450 MB | an image build's output |
| Local artefacts | about 450 MB per build, plus the builder's cached images, consoles, cards and sources | `.local/`, ignored by Git, never published from there |
