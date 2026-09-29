# mun-launchd

Starts a game from the active Game Card and gets the shell back, whatever
happens to the game. What a game may expect: [docs/runtime.md](../../docs/runtime.md);
saves: [docs/saves.md](../../docs/saves.md).

## Flow

1. The shell sends `launch {slot, serial, version}` over `/run/mun/launchd.sock`.
2. launchd checks, at that moment, that mun-cardd is connected and that the
   active card is exactly that slot/serial/version, valid, `kind = game` with
   an entry. Otherwise it answers `busy`, `reader_unavailable`, `card_mismatch`
   or `not_runnable` and nothing changes.
3. It creates `/run/mun/launch/<sid>/` (root:mun-game 0750, `work/`
   owned by the game user) and asks cardd, over the root-only control socket,
   to copy the entry there. cardd reads the file from its private mount
   (regular file only, ≤ 64 MiB, no symlinks, and it must start as a
   little-endian 64-bit ELF for AArch64; anything else is `entry_not_executable`
   and nothing is copied), writes `game.part`, and only when complete makes it
   `0555` and renames it to `game`. If the card goes away during the copy,
   cardd aborts and removes the partial file.
4. launchd stops the shell, then starts `mun-game-<sid>.service` with
   `systemd-run`: uid/gid `mun-game`, groups video+input, own cgroup,
   `DevicePolicy=closed` with only `/dev/fb0` and input devices allowed,
   `PrivateNetwork`, `ProtectSystem=strict`, `NoNewPrivileges`, an empty
   `CapabilityBoundingSet`, `MemoryMax`, `TasksMax`, `TimeoutStopSec=3`,
   `KillMode=control-group`, environment `MUN_CARD_ID`,
   `MUN_CONTENT_VERSION`, `MUN_SESSION` (see *Game environment* below). The unit is not `--collect`ed:
   a crashed unit keeps its `Result` and exit code until launchd has read them,
   then `systemctl reset-failed` removes it.
5. The unit ends (Esc → exit 0, crash, kill, timeout): systemd starts the
   session's cleanup unit (removes the copy), and within the next poll
   (0.5 s) launchd records the result from the game's own process, waits for
   a directory save's last write if there is one, removes the session
   directory and starts the shell. The shell reads `/run/mun/launch/last-result.json` at start
   or receives the result over the socket a moment later; either way it skips
   the splash and shows what happened until the player acknowledges. The
   acknowledgement is persisted by launchd; if launchd was down at that moment
   the shell repeats it on reconnection, so the same result is never shown twice.
   A failed staging (bad entry, card gone) never stops the shell: the result
   dialog simply opens over the card sheet.

## Runtime profiles

The manifest's `content.profile`, accepted by the validator, decides what the
unit may touch; everything else about the unit is common (no capabilities,
no network, a read-only system with `work/` and the unit's private `/tmp`
and `/var/tmp` writable, the fixed command path). `linux-arm64-v0` grants `/dev/fb0` and evdev with the groups
`video` and `input`. `linux-arm64-gl-v0` (provisional) grants the
DRM class (card and render nodes), the ALSA class and evdev, groups `video`,
`render`, `audio`, `input`, 2 GiB and 64 tasks, and sets
`SDL_VIDEODRIVER=kmsdrm`, `SDL_AUDIODRIVER=alsa`, `ALSOFT_DRIVERS=alsa` so
SDL and OpenAL Soft do not probe display and sound servers the console does
not run. Every game receives `MUN_RUNTIME_PROFILE`. `deploy/sysusers.conf`
adds the game user to the two groups; the image installs the system
libraries the GL profile promises and records their versions in its
BUILD-INFO.

## Game environment

Every variable a game receives is published under its `MUN_` name. For a card
of the earlier naming generation (`neptune.toml`) it is also published, with
the same value, under the `NEPTUNE_` name games were built against before the
MUN naming (`unit_environment`, one table;
[naming generations](../../docs/game-cards.md#naming-generations)): MUN Collect
binaries on those cards read `NEPTUNE_SAVE_FILE` and `NEPTUNE_SAVE_SOCKET`. A
card with `mun.toml` gets `MUN_*` only. The card's generation is kept in the
session record, so an adopted session keeps it. The names are
`CARD_ID`, `CONTENT_VERSION`, `SESSION`, `RUNTIME_PROFILE`, `CONTENT_DIR`
(mount access), `SAVE_FILE` and `SAVE_SOCKET` (single-object saves).

## Card content during play (`content.access = mount`)

For a card whose manifest declares `mount`, the card service's staging reply
names the card's block device and `content.root`. The launcher accepts only
a plain `/dev/<name>` node and a safe relative root (anything else cancels
the launch with `prepare_failed`), then adds to the unit
`MountImages=-<device>:/run/mun/card:root:ro,nosuid,nodev,noexec` and
`DeviceAllow=<device> r`, and sets `MUN_CONTENT_DIR=/run/mun/card/<root>`.
`/run/neptune/card`, the path before the MUN naming, is a link to
`/run/mun/card` (`deploy/tmpfiles.conf`): engines built with it compiled in,
on earlier cards, still find their data.
PID 1 mounts the device inside the unit's own mount namespace; the mount
disappears with the unit and never appears in the console's namespace. The
device node stays `root:disk 0660`, so the game cannot read the raw card
even though the unit's device cgroup admits it for the mount set-up. The
leading `-` keeps the cleanup hook running when the device is already gone
(the launcher ends the session on removal anyway). Why the device and not
the service's mount: that mount lives in the service's private namespace
and cannot be bound from outside; the same superblock is what the
service validated, and its read-write window for a save never reaches the
game's own read-only mount.

## Ending a session early

- Card removed (event from cardd naming the session's insertion) or card
  missing when cardd reconnects: `systemctl stop` the unit (SIGTERM, 3 s,
  SIGKILL), result `card_removed`.
- cardd unreachable for more than 5 s: result `reader_lost`, same stop path,
  also for a game adopted after a launcher restart (the grace starts at
  adoption). The service never assumes a card is still present without cardd
  saying so.
- Continuity on reconnection is proven by cardd's insertion token stored in
  the session, never by the slot (which restarts at `c1`). If the token
  differs because cardd itself restarted, the same serial, card id and content
  version are accepted as the same card and logged; anything else in the
  reused slot ends the session. After staging, only the staged insertion may
  start.
- While a session is still `preparing`, that identity is frozen: a copy in
  flight was authorised against one insertion, so a token change cancels the
  preparation (`prepare_failed`) instead of adopting the new card. A staging
  reply that arrives afterwards finds no session and starts nothing. Only a
  reconnection carrying the same token keeps a preparing session alive.
- `MUN_LAUNCHD_READER_GRACE` overrides the 5 s grace for experiments
  (unset in the installed unit); it exists so a timing test can exercise the
  identity check instead of the clock.
- The card feed is read through a bounded line reader (2 MiB per line, the
  budget cardd enforces); an unfinished line beyond it means a protocol break
  and the feed is reconnected for a fresh snapshot.
- Preparation or start taking too long: `prepare_failed` / `start_failed`.

## Recovery without the launcher

- Every game unit carries `OnSuccess=` and `OnFailure=`
  `mun-launch-cleanup@<sid>.service`: whenever the unit stops (exit 0,
  stop, crash, kill, non-zero exit), systemd itself starts that oneshot unit,
  which removes the staged copy and, if no launcher is active and no other
  game unit runs, starts the shell. No dependency on launchd being alive. It
  is a unit of its own, in the host's mount namespace, not an
  `ExecStopPost=` of the game unit: every process of the game unit gets its
  `MountImages=`, and while the card service holds the card read-write for
  a save the kernel refuses a new read-only mount of it ("Can't mount, would
  change RO state", `EBUSY`), so a hook in there never ran and the unit
  failed although the game had exited 0. The game unit's
  `Result` is now only the game's.
- `mun-launchd.service` has `Restart=always` and `OnFailure=` the restore
  unit, which starts the shell if no game unit is running.
- On start, launchd reconciles: an active game unit with a `session.json` is
  adopted and supervised to its end; stale units are stopped and stale session
  directories removed and reported as an `interrupted` result, so a player who
  pressed Play just before the launcher died is told instead of silently
  getting the shell back; if nothing is running the shell is started.
- The cleanup unit removes only the staged copy. The rest of the session
  directory is the session's record (`session.json`) and, for directory
  saves, what the last capture reads: launchd, running now or at its next
  start, records the result from it before removing it. With launchd active
  the shell is left to launchd, which starts it once the session's last save
  has been answered. A removal or shell start that fails leaves the cleanup
  unit `failed` (visible in `systemctl --failed` and the journal); launchd
  reports that apart from the game's ending and cleans up itself.
- launchd owns the cleanup's outcome until it is known. The unit is
  `RemainAfterExit=yes` (success stays `active (exited)`, so a finished
  cleanup is never confused with one not started yet) and bounds itself
  with `TimeoutStartSec=10s`. When a session ends, launchd looks once: done
  → released (`systemctl stop`); failed or not installed → reported with the
  result, a failed unit then `reset-failed`; still pending → watched every
  tick, by session id, for up to `CLEANUP_TIMEOUT` (15 s). A late failure, or
  a timeout ("no terminó en 15 s"), is added to that session's stored result
  as a `platform` entry and the result is shown again (the shell re-shows an
  acknowledged result only when such a notice is new); if a later session's
  result has replaced it, the notice stays in the journal and goes to no other
  session. A session whose unit never started waits for no cleanup. At
  start, launchd sweeps cleanup units left while no launcher watched:
  finished ones are released, failures go with that session's `interrupted`
  record (or its stored result), running ones are watched.
- The game's ending is judged from its own process (`ExecMainCode`,
  `ExecMainStatus`): a signal or a core dump is `crashed`, a non-zero exit
  `failed`, exit 0 `exited`, a unit that never started its process
  `start_failed`. A unit `Result` other than `success` under a clean exit is
  shown as a separate `platform` line in the result, never as the game's
  error and never dropped.
- The installer starts the launch tmpfs but never remounts it: a redeploy
  during a session must not wipe the running copy.

## Saves

Right before the game unit starts, launchd opens `<session>/save.sock`
(root:mun-game 0660) and hands the game `MUN_SAVE_SOCKET` and
`MUN_SAVE_FILE` (`work/save.json`, the current save the card service
copied at staging). The game connects once per save and sends one line,
`{"type":"save","schema":N,"data":{…}}`, at most 64 KiB plus framing. launchd
accepts it only while that session is `running` and the reader is connected,
one save at a time, and forwards it to the card service bound to the session's
id and insertion token; the card service refuses any other insertion. The
reply (`saved {ok, bytes}` or `saved {ok: false, error}`) reaches the game
only if the session still exists when it comes back: a late confirmation for a
session that ended is logged and dropped. The socket is closed with the
session; an adopted game gets a fresh one.

## Directory saves

A card that declares `saves.directory` (for example `.mygame/save`) is saved by
the console; the game is unmodified and gets neither `MUN_SAVE_FILE` nor
`MUN_SAVE_SOCKET` (a socket save from it is refused, here and by the card
service). Per session:

- **Before the unit starts** launchd creates the directory in `work/` (owned
  by the game user), checks the envelope the card service staged (format,
  card id, `payload_kind = "files"`, every name a single segment matching a
  declared unit, sizes within `max_bytes`, every SHA-256, every unit check)
  and writes the units there, or refuses the whole envelope and says so.
  The directory is reached one component at a time from the session
  directory (root-owned), each step `O_NOFOLLOW|O_DIRECTORY` relative to the
  descriptor before it, so no component the game owns can be a link; the
  inotify watch is added on `/proc/self/fd/<descriptor>`, the directory held,
  not its path.
  `<session>/sync/` (root 0700) holds payload files and `synced.json`, the
  last snapshot the card confirmed.
- **During play** a unit closed after writing (`IN_CLOSE_WRITE`) or renamed
  into place triggers a capture at the next tick: `systemctl freeze` on the
  unit (confirmed by `FreezerState=frozen`), drain the events, then for each
  unit open read-only without following links, refuse anything that is not
  a regular file with one link, take a read lease (`F_SETLEASE F_RDLCK`,
  refused by the kernel while any process has the file open for writing or
  mapped shared and writable), read it within the bound, and `systemctl
  thaw`. A refused lease, a lease that cannot be taken or a failed freeze
  means no save point: nothing is taken and the next close tries again.
  Measured in a QEMU guest with a GL-profile game: 24–36 ms from freeze to thaw.
- **Completeness** per unit: `zlib` (a whole stream with its end marker),
  `zlib-xml` / `xml` (well-formed once wrapped in one root; one final NUL,
  a C string terminator some engines write, is ignored), `any`. A unit that fails, or
  vanished, keeps its version from `synced.json` (carried); a new unit that
  fails is left out. Both are reported. A snapshot equal to the last one
  written is not written again.
- **To the card** the snapshot is a payload file handed to the card service
  by path, size and SHA-256 (`payload_kind = "files"`), one write at a time.
- **After the unit stops** the session state becomes `saving`. Every event
  the game caused is queued by then and is accounted for first, and again
  once a write still in flight has been answered (a game can save and quit
  between a poll and the moment its stop is seen); then one last capture
  without freezing (nothing runs) of whatever the card does not hold yet,
  a failed write included, at most once. The shell comes back once the
  card has answered or after `SAVE_TIMEOUT + 5` s. A card that was removed
  or a lost reader gets no write; the unsynced changes are reported.
- **Adoption** after a launcher restart thaws the unit (a capture may have
  been cut short), walks to the directory again as above, reloads
  `synced.json`, rebuilds the watch and captures at the next tick. A
  component the game replaced meanwhile (a link, a file) makes the walk
  fail: nothing is watched or read, and the result says the session's save
  could not be kept.
- **Startup** records a session that ended while no launcher ran as
  `interrupted`, with "No se pudo comprobar si la última partida llegó a la
  Game Card" for directory saves, before its directory is removed: whether
  its unit is still listed (failed and kept for its result, or inactive) or
  gone. The card keeps its last written envelope.

The session result gains `saves {ok, message, synced_at, carried, omitted,
error, restore}`; the shell shows `message` under the result line, in the
danger colour when `ok` is false.

`launchd.py release <serial>` is the safe-removal CLI used by the laboratory tool: it
refuses with `in_use` while a session uses that card, otherwise asks the card
service to release it and prints the JSON reply.

`launchd.py cards` prints the card service's current records (slot, serial,
state, insertion, active, device) as one JSON line, read from the snapshot
cardd sends every client; no manifest or cover. The laboratory uses it to
decide whether the current insertion of a card was released before
unplugging it.

## Where the executable crosses

cardd's mounts live in its private mount namespace; launchd never sees them.
The copy is the only thing that crosses, into `/run/mun/launch`, an
exec-capable tmpfs (`run-mun-launch.mount`, 128 MiB, `nosuid,nodev`)
shared by both services because plain directories under `/run/mun` are
the same tmpfs in every namespace. `/run` itself stays `noexec`.

## Privileges

launchd: root for `systemd-run --uid` and `systemctl`, with
`PrivateDevices`, `PrivateNetwork`, `ProtectSystem=strict`, writes only under
`/run/mun`, capabilities `CHOWN FOWNER DAC_OVERRIDE DAC_READ_SEARCH KILL
LEASE` (the lease proves no writer on a save file the game owns).
It ignores `SIGIO`, the signal a lease break would send it.
It differs from cardd on purpose: no `CAP_SYS_ADMIN`, no block devices, but
it may talk to PID 1. The game: unprivileged user, cannot modify services,
the copy (`0555`, root-owned) or anything outside its `work/` directory, its
private temporary directories and the shared-memory file systems.

## Install

Two ways, the same files. A MUN OS image build (`os/`) runs
`deploy/stage.sh SRC DESTDIR` into the image: the launcher, the cleanup
helper, the exec tmpfs, the cleanup and restore units and the launcher unit,
and `deploy/sysusers.conf` (the `mun-game` user and its groups). Its
runtime packages and the GL profile's libraries (`deploy/packages`,
`deploy/runtime-linux-arm64-gl-v0.packages`) are in `os/mkosi/mkosi.conf`
and in the image's BUILD-INFO.

## Operate

```sh
python3 tools/mun-card/mun-card create game --variant game --game .local/mun/builds/one/games/mun-collect/mun-collect
./mun vm launchd-log      # one line per accepted/rejected launch, staging, result and cleanup
./mun vm run -- systemctl list-units 'mun-game-*'
./mun vm run -- systemctl start mun-shell   # manual recovery if ever needed
```

Limits of this version: the game runs from a temporary copy and the card must
stay present during the session; assets are not streamed from the card and
execution directly from the medium has not been tried.
