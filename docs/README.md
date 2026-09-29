# Documentation

## Using and building

- [README](../README.md): what MUN OS is, what is available, first steps.
- [Image build](../os/README.md): how a MUN OS image is composed, what a
  build records, reproducibility.
- [Laboratory](../vm/README.md): builds and QEMU guests on a Mac, Game Cards,
  input, logs.
- [Contributing](../CONTRIBUTING.md): checks, tests and commits.

## Contracts

- [Game Cards](game-cards.md): image, manifest, naming generations,
  conversion.
- [Saves](saves.md): storage on the card, single-object and directory saves,
  removal and failures.
- [Running a game](runtime.md): launch, sandbox, runtime profiles,
  environment, content during play, session results.

## Reference

- [Architecture](architecture.md): components, boundaries, what the hardware
  integration will own, what is not implemented.
- [Languages and native boundaries](development/languages.md).
- [Glossary](glossary.md).

Components document their own protocols, units and privileges:
[MUN Shell](../services/mun-shell/README.md),
[card service](../services/mun-cardd/README.md),
[launcher](../services/mun-launchd/README.md),
[`mun-card`](../tools/mun-card/README.md),
[examples](../examples/README.md) and [tests](../tests/README.md).
