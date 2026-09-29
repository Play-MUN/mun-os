# Contributing

MUN OS is at an early stage: one development image for QEMU, no release and
no selected hardware. Read the [README](README.md) and the
[architecture](docs/architecture.md) first; the contracts a change must keep
are in [docs/](docs/README.md).

## Set up

The requirements and the first build are in the [README](README.md#requirements).
Host checks need only Python 3.9 or later and Make; a C compiler adds the
example game's save tests.

## Checks

```sh
make check      # documentation links, Python syntax, host regressions, C save tests
make test       # the Python host regressions only
```

`make check` needs no network and boots no VM. It does not build the shell or
an image: `./mun dev build` does, and a change to a service, the image
composition or the laboratory should be tried in a guest as well
([vm/README.md](vm/README.md)). Say in the pull request which checks you ran
and which you could not.

[Lefthook](https://lefthook.dev/) runs `make check` before each commit when
installed in the checkout (`lefthook install`); `lefthook-local.yml` is
ignored and can extend it locally.

## Changes

- Keep a change to one purpose, and keep the working game path (insert,
  play, save, remove, continue) usable.
- Never break existing Game Cards, their saves or the games on them. A name
  or behaviour that cards, saves or games depend on changes only with a new
  version of its contract, the earlier one still honoured.
- Treat card contents and removable-media paths as untrusted input, and
  invalid data and disconnected devices as recoverable errors.
- Keep the shell unprivileged and separate from device and mount operations.
- Nothing in offline play may depend on an account or a network service.
- Document ownership, lifetime, error, thread and ABI contracts at native
  boundaries ([languages](docs/development/languages.md)).
- Explain non-obvious reasons in comments; do not restate the code.
- Distinguish what was measured on hardware, what was observed in a VM, what
  a mock shows and what is a hypothesis.

## Tests

Host tests live in `tests/` and use Python's `unittest` with the standard
library only, temporary files and fake process and protocol boundaries; they
need no network, QEMU or root ([tests/README.md](tests/README.md)). Add a
regression test with each fix. Behaviour that only a running guest can show
is described in the pull request with the commands used.

## Commits and branches

- Work on a short-lived branch from `main` (`feat/…`, `fix/…`, `docs/…`) and
  open a pull request against `main`.
- Use [Conventional Commits](https://www.conventionalcommits.org/) and your
  own Git identity.
- Inspect `git diff --cached` before committing.
- Do not rewrite shared history.

## What stays out of Git

Everything generated lives in `.local/`, which Git ignores: builds, guests,
card images, keys and logs. Never commit game data you do not have the right
to distribute, commercial installers, credentials or personal tool
configuration; keep the latter in `.git/info/exclude` or your global ignore
file rather than in `.gitignore`.

## Licence

MUN OS's own work is licensed under the [Apache License 2.0](LICENSE), and
so are contributions to it: as section 5 of the licence states, what you
intentionally submit for inclusion is under its terms, with no additional
conditions, unless you explicitly say otherwise. This repository carries no
third-party game: a port of a game you own belongs in a recipe you keep
outside it ([game recipes](os/README.md#game-recipes)), under that game's
own terms.

Submit only what you may license this way. Code, fonts, images or other
material from elsewhere keep their own terms: say where they come from and
under which licence, so the [README](README.md#licence) can list them. The
MUN name and logo are not licensed ([README](README.md#licence)).
