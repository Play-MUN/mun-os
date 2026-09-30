# Security

MUN™ OS is experimental: a development image for QEMU, not a supported
release, with no hardware. It still has security boundaries meant to hold,
and reports about them are welcome.

## Reporting a vulnerability

Report it privately, not in a public issue:

- on GitHub, **Security → Report a vulnerability** in this repository, or
- by e-mail to **hello@playmun.com**, with "security" in the subject.

Say what is affected (a commit or a preview release, and `BUILD-INFO.json`'s
`build_id` if an image is involved), how to reproduce it, and what it lets
someone do. The maintainer answers and keeps you informed; a fix lands on
`dev`, then on `main` and in the next preview release. Please give that time before telling
anyone else, and say whether you want to be credited.

## What is in scope

What MUN OS promises to protect:

- **Game Cards as untrusted input**: the card service's validation, the
  manifest and path rules, covers, saves and the card tool that reads them
  (`services/mun-cardd`, `tools/mun-card`).
- **The game's sandbox**: what a game unit can reach, the launcher's control
  of it, saves written for it (`services/mun-launchd`,
  [running a game](docs/runtime.md)).
- **Privilege separation**: the unprivileged shell and the narrow helpers it
  may call (`services/mun-shell`).
- **Downloads**: `./mun get`'s verification of a release against its
  `release.json` and its handling of existing Game Cards (`vm/bundle.py`).

Out of scope: QEMU and the host computer's own security, the laboratory's
development access into a guest (qemu-guest-agent, root by design in the
development image), and denial of service by a card or game the player chose
to insert.

## Supported versions

Only the latest preview release, `main` and `dev`. Earlier previews get no fixes.
