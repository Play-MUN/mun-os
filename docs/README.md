# Documentation

## Start here

- [README](../README.md): what MUN OS is, what works today.
- [Getting started](getting-started.md): download the tools and an image,
  start the console, play, save and continue.
- [Compatibility](compatibility.md): where playing, making cards and
  building images work, and how that was checked.

## Guides

- [Create a Game Card](guides/create-game-card.md): MUN Collect on a card of
  your own, from the executable to a save restored after a restart.
- [Bring a game to MUN](guides/port-a-game.md): runtime profiles, libraries,
  data and saves, recipes; why packaging a game is not porting it.

## Contracts

- [Game Cards](game-cards.md): image, manifest, naming generations,
  conversion.
- [Saves](saves.md): storage on the card, single-object and directory saves,
  removal and failures.
- [Running a game](runtime.md): launch, sandbox, runtime profiles,
  environment, display, content during play, session results.

## Building and reference

- [Image build](../os/README.md): how a MUN OS image is composed, what a
  build records, game recipes, reproducibility.
- [Laboratory](../vm/README.md): builds and virtual consoles, Game Cards,
  input, logs.
- [Architecture](architecture.md): components, boundaries, what the hardware
  integration will own, what is not implemented.
- [Languages and native boundaries](development/languages.md).
- [Glossary](glossary.md).

## The project

- [Contributing](../CONTRIBUTING.md), [security](../SECURITY.md),
  [releasing](releasing.md).
- [Licensing](licensing.md): MUN OS's licence, third-party components,
  images and their source, the MUN name and logo.

Components document their own protocols, units and privileges:
[MUN Shell](../services/mun-shell/README.md),
[card service](../services/mun-cardd/README.md),
[launcher](../services/mun-launchd/README.md),
[`mun-card`](../tools/mun-card/README.md),
[examples](../examples/README.md) and [tests](../tests/README.md).
