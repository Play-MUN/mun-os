# mun-reset

*Settings* › *System* › *Reset console*: the console returns to how it was
the first time it started. It is not *Reset settings*, which keeps the
language and touches nothing but the settings file.

What it never does:

- touch a Game Card, a game or a save: it refuses while a card is in the
  slot, and it deletes nothing outside its plan;
- run during a game, or while a game starts;
- reinstall the system, go back to an earlier version, or erase securely:
  the files are deleted, not overwritten;
- need a network.

## What it deletes and what it keeps

| Path or data | What it is | Reset console | Managed by |
| --- | --- | --- | --- |
| `/var/lib/mun-shell/settings.ini`, and `/var/lib/mun-shell/settings.ini.*` (a write cut short, its lock) | the player's settings, the language included | deleted: the next start is in English with the defaults | MUN Shell |
| `/var/cache/mun-shell/` | the shell's font cache | emptied; fontconfig makes it again | MUN Shell, fontconfig |
| `/var/log/journal/` | the system's history: which cards and games were used, errors | emptied | journald |
| `/var/log/wtmp`, `/var/log/btmp`, `/var/log/lastlog` | the record of boots and sessions | emptied | systemd, login |
| `/etc/machine-id` | this installation's identifier | kept | systemd |
| `/var/lib/systemd/` (random seed, timers, catalogue), `ldconfig`'s caches | the system's own state | kept | systemd |
| `/run/mun/`, `/run/mun-shell/` | what the services hold while running | gone with the restart that carries out the reset | the services |
| A Game Card: its device, its mount at `/run/mun/card`, its game, its saves, its MUN Shape | the player's | **never**: the reset refuses while a card is in the slot | mun-cardd |
| `/usr`, `/opt/mun`, `/boot`, `/etc` | the installed system and its version | kept | the image |
| In the laboratory image: `/etc/localtime`, `/etc/timezone` | the host's time zone | kept (`mun-lab-timezone` sets it again) | the `qemu-dev` profile |
| An account, digital content, network credentials, pairings | none of these exist today | not included by assumption: when one exists, whether a reset takes it is decided then | — |

`PLAN` in `reset.py` is this table's *deleted* and *emptied* rows, and
nothing else; `tests/test_reset.py` checks that the two agree.

## How it runs

1. **`mun-reset request`**, as root through the shell's sudoers rule
   (`services/mun-shell/deploy/mun-shell.sudoers`, this exact command). It
   refuses, with its exit status, when:

   | Status | Why |
   | --- | --- |
   | 65 | a Game Card is in the slot, in any state (being read, valid, not valid, released but not removed) |
   | 66 | the launcher is not idle: a game is being prepared, starts, runs, saves or stops |
   | 67 | the console is shutting down |
   | 68 | the card service or the launcher did not answer |

   Otherwise it starts `mun-reset-commit.service` and waits for it.
2. **`mun-reset commit`**, in that unit (`deploy/mun-reset-commit.service`,
   which nothing else starts). It runs as a unit of its own because the
   shell's sandbox (`ProtectSystem=strict`) keeps the file system read-only
   for the shell and for whatever it runs, sudo included. It writes the
   mark, `/var/lib/mun-reset/pending`, whole or not at all (a temporary
   file, synced, renamed, its directory synced), and restarts the console.
   If the restart cannot be asked for, it removes the mark again: no reset
   happens at a later boot nobody asked for.
3. **`mun-reset apply`**, at the next boot (`deploy/mun-reset.service`,
   only when the mark exists). It runs before the system journal moves to
   the disk, before the boot is recorded and before the console's services
   start, deletes what the plan lists, syncs, and removes the mark last.
   Every step can run again, so a boot cut short (power lost, a crash)
   leaves the mark and the next boot finishes the reset. A path in the plan
   that is a symbolic link is removed as a link, never followed.
4. The console starts as on its first day, start-up included.

Any failure is one line on the standard error, for the shell to show; never
a traceback.

## Tests

`tests/test_reset.py` (part of `make check`): the plan against this table;
a reset on a copy of a console's files, run twice; a reset cut short after
each step and finished by the next; nothing outside the plan touched, a
Game Card's files hashed before and after; links not followed; the mark
written whole; the refusals against stand-ins for the card service and the
launcher. The laboratory checks it in an image guest.
