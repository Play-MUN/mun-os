# Glossary

**English** · [Español](es/glossary.md)

Terms used across the MUN™ documentation and code.

| Term | Meaning |
| --- | --- |
| BOP | Buy. Own. Play. A legitimate Game Card is enough to own and play its game offline; accounts are optional |
| MUN | The console and the project |
| MUN -1 | The first physical console generation, with one officially supported hardware configuration, not selected yet |
| MUN OS | The console's operating system. So far only a development image for QEMU exists |
| MUN Shell | The console's interface, `mun-shell` |
| Game Card | Removable medium holding a game, its presentation and its saves ([game-cards.md](game-cards.md)). In the laboratory, a raw ext4 image hot-plugged into a QEMU guest |
| Manifest | The card's descriptor at its root, `mun.toml` (or `neptune.toml` on an earlier card), validated as untrusted input |
| Naming generation | Earlier cards carry `neptune.toml` and save as `neptune-save/1`; current ones `mun.toml` and `mun-save/1`. The console reads both and writes each card's own ([game-cards.md](game-cards.md#naming-generations)) |
| Insertion | One insertion of a card, identified by a random token the card service mints; a session is bound to it |
| Active card | The one card the console uses at a time, the first inserted; a second one waits |
| Safe removal | Releasing a card before it is pulled: no session uses it, writes are finished, it is unmounted strictly (Eject safely in the shell) |
| Session | One run of a game from a card, supervised by the launcher, with one result |
| Runtime profile | What a game may use and must provide: `linux-arm64-v0` (framebuffer) or `linux-arm64-gl-v0` (DRM, OpenGL, audio) ([runtime.md](runtime.md)) |
| Save envelope | The JSON file the console writes on the card around a game's save ([saves.md](saves.md)) |
| Directory save | A save the console makes of files the game writes itself, declared as units in the manifest |
| Save unit / save point | A unit is a file the card declares complete by itself, with a completeness check; a save point is an instant the console can verify (game frozen, no unit open for writing, every unit complete), the only moment it copies units to the card |
| Laboratory | The QEMU development, test and fault-injection environment under `vm/`; not a product platform |
| Builder | A disposable QEMU VM that builds one MUN OS image and is deleted afterwards |
| Image guest | A QEMU guest booted from a development build, with no network interface |
| BUILD-INFO | The machine-readable record of a build: sources, packages, toolchain, configuration and hashes ([os/README.md](../os/README.md)) |
