# Contributing

**English** · [Español](CONTRIBUTING.es.md)

MUN™ OS is at an early stage: one development image for QEMU, no supported
release and no selected hardware. Contributions are welcome: fixes, tests,
documentation, card and game tooling, ports of the laboratory to more
computers. Read the [README](README.md), [getting started](docs/getting-started.md)
and the [architecture](docs/architecture.md) first; the contracts a change
must keep are in [docs/](docs/README.md).

## Who decides

MUN OS is founded and maintained by Iván Moreno Mendoza (Play MUN). The
maintainer reviews pull requests and decides what goes into `dev` and, from
it, into the official version, `main` and its releases; a fork may of
course go its own way
([licensing](docs/licensing.md#the-mun-and-play-mun-names-and-logos) says
how it may name itself).

## Before you start

- **Small fixes and documentation**: open a pull request directly.
- **Anything larger** (a new feature, a change of behaviour that a card, a
  save or a game can see, a new dependency or component): open an issue first and
  describe the problem and the proposal, so it can be discussed before you
  spend time on it. Architectural changes are agreed in the issue before
  they are implemented.
- Security problems go privately to the maintainer, not to an issue
  ([security](SECURITY.md)).

## Set up and checks

The requirements to play and to build are in
[compatibility](docs/compatibility.md). Host checks need only Python 3.9 or
later and Make; a C compiler adds the example game's save tests.

```sh
make check      # documentation links, anchors and translations, Python syntax, host regressions, C save tests
make test       # the Python host regressions only
```

`make check` needs no network and boots no VM; pull requests run it
automatically on Linux and macOS. It does not build the shell or an image:
`./mun dev build` does, and a change to a service, the image composition or
the laboratory should be tried in a guest as well ([laboratory](vm/README.md)).
Say in the pull request which checks you ran and which you could not.

[Lefthook](https://lefthook.dev/) runs `make check` before each commit when
installed in the checkout (`lefthook install`); `lefthook-local.yml` is
ignored and can extend it locally.

## Changes

- Keep a change to one purpose, and keep the working game path (insert,
  play, save, remove, continue) usable.
- **Never break existing Game Cards, their saves or the games on them.** A
  name or behaviour that cards, saves or games depend on changes only with a
  new version of its contract, the earlier one still honoured.
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
regression test with each fix. The shell's behaviour regressions run on its
compiled binary in every image build (`./mun dev build`,
[services/mun-shell/tests/behaviour.py](services/mun-shell/tests/behaviour.py)).
Behaviour that only a running guest can show is described in the pull
request with the commands used.

## Documentation and translations

Documentation is written in English, the technical reference. Some pages
also have a Spanish translation: `docs/es/` mirrors `docs/`, a page elsewhere
has `NAME.es.md` beside `NAME.md`, and each pair is linked both ways by an
English / Español line. `docs/translations.json` lists every translation
with the SHA-256 of the English text it was last reviewed against.

When you change a page that has a translation, update the translation in the
same change if you can, or say in the pull request that it needs one.
`make check` fails until the translation has been reviewed against the new
English text and its `source_sha256` set to the value the check prints; set
it only after reading both. Commands, options, paths, file names, JSON and
TOML fields and error codes stay as they are in a translation, and the
output of a tool is quoted in the language the tool prints.

## Branches, commits and pull requests

A change travels this way:

1. A short-lived branch from `dev`.
2. A pull request against `dev`, with its checks green, reviewed and
   approved by the maintainer.
3. Merged into `dev`, where changes stay until a preview is prepared.
4. A pull request from `dev` to `main`, the candidate checked as
   [releasing](docs/releasing.md) says, and the release tagged on `main`.

In detail:

- `dev` is where the work lands first. Fork the repository, or, with write
  access, work on a short-lived branch from `dev`: `feat/…`, `fix/…`,
  `docs/…`, `chore/…`.
- `main` holds what is released: it receives `dev` through a pull request,
  merged with a merge commit so that both keep one history, when a preview
  is prepared, and releases are tags on tested commits of `main`. Changes
  reach either branch only through pull requests with the checks green.
- Use [Conventional Commits](https://www.conventionalcommits.org/) and your
  own name and e-mail in Git: your contribution stays yours.
- Sign off every commit (`git commit -s`, below).
- Inspect `git diff --cached` before committing, and do not rewrite history
  others have.
- Open the pull request against `dev` (GitHub proposes `main`, the branch
  players get: change the base), fill in its template and keep it focused;
  pull requests into `dev` are squashed when merged.

## Rights and sign-off

MUN OS's own work is licensed under the [Apache License 2.0](LICENSE), and
so are contributions to it: as section 5 of the licence states, what you
intentionally submit for inclusion is under its terms, with no additional
conditions, unless you explicitly say otherwise. Your contribution remains
yours; you license it, you do not transfer it.

The `Signed-off-by:` line `git commit -s` adds certifies the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):
that you wrote the change or otherwise have the right to submit it under the
project's licence. Submit only what you may license this way. Code, fonts,
images or other material from elsewhere keep their own terms: say where they
come from and under which licence, so [licensing](docs/licensing.md) can
list them. The MUN and Play MUN names and logos are not licensed.

## What stays out of Git

Everything generated lives in `.local/`, which Git ignores: builds, guests,
card images, keys and logs. This repository carries no third-party game: a
port of a game you own belongs in a recipe you keep outside it
([bring a game to MUN](docs/guides/port-a-game.md)), under that game's own
terms. Never commit game data you do not have the right to distribute,
commercial installers, credentials or personal tool configuration; keep the
latter in `.git/info/exclude` or your global ignore file rather than in
`.gitignore`.

## Releases

How a preview release is made, and what it must carry, is in
[releasing](docs/releasing.md).
