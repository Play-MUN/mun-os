# mun-cardd

Game Card service for the console: detects cards, checks and mounts them
read-only, validates the manifest with the shared `mun_card` package,
publishes state to the shell and the launcher over a local UNIX socket,
stages a game's entry and writes saves. Card format:
[docs/game-cards.md](../../docs/game-cards.md); saves:
[docs/saves.md](../../docs/saves.md).

## Behaviour

- Eligible devices: virtio disks whose serial starts with `NPT-` (what
  `./mun vm card-attach` assigns) and, when the image names a board's card
  reader in `MUN_CARDD_SLOTS` (sysfs paths of the reader's controller,
  separated by `:`; none by default), the whole card in that reader, named
  `NPT-sd-` and the card's CID. Only a whole disk whose sysfs path lies
  under a named controller counts: the board's other disks, its system
  eMMC among them, never do, although the device policy that lets the
  service open the reader's MMC devices also covers them. System disks and
  anything else are never inspected or mounted.
- Present at start and inserted later: udev events plus a sysfs rescan every
  5 s as a safety net.
- Before mounting: whole-image ext4, clean superblock, no recovery flag, no
  partition table. Mount options `ro,nosuid,nodev,noexec,errors=remount-ro`,
  with the journal loaded (see *Saves*), under `/run/mun/cards/<slot>`;
  `<slot>` is `c1`, `c2`… chosen by the service.
- Validation runs in a worker thread; results for a card removed meanwhile are
  discarded (generation counter). Invalid cards are published as such and
  then unmounted, and the journal names each one once per insertion: slot,
  device, serial, code and reason (`c1 (vdc, NPT-card01) refused:
  image_has_errors: …`).
- One active card. A second card waits unmounted with reason
  `another_card_active`; when the active card is removed the waiting one is
  evaluated automatically. Nothing is launched, ever.
- Removal: unmount (lazy if busy), `removed` event, promotion of a waiting card.
- An unmount that cannot even start (no process to be had: the service's
  task limit, memory) leaves the mount as it is and recorded: the card is
  still published as refused or removed, the service's loop tries its
  unmount again every 5 seconds (no thread is needed for that, nor for any
  timer whose thread cannot start), and a safe release meanwhile is
  answered `unmount_failed`, never `released`, whether the card is still
  in or already gone.
- For a valid card, its MUN Shape package is copied to RAM for the shell
  ([MUN Shape export](#mun-shape-export)).

## Contract (protocol 1)

Socket `/run/mun/cardd.sock`, root:mun-shell 0660, one JSON object per
line, server to client only.

```json
{"type":"snapshot","protocol":1,"reader":"ready","cards":[<card>...]}
{"type":"card","card":<card>}
{"type":"removed","slot":"c1","insertion":"3f9a…","device":"vdc","serial":"NPT-card01"}
```

`<card>` = `{slot, insertion, device, serial, state, active, info, error, shape}`
with `state` ∈ `reading | valid | invalid | waiting`, `info` the validated
manifest (`id, title, version, kind, arch, profile, root, entry, cover, saves,
runnable, schema`) plus `cover_data` (base64 PNG, at most 1 MiB, only
when a cover exists), `error` = `{code, message, detail}` or null. Mount points
stay inside the service's private mount namespace (systemd sandbox); no path
crosses the socket and the shell needs no access to `/run/mun/cards`. A client
that connects receives the snapshot first; a client that loses the socket
shows "reader unavailable" and reconnects.

`shape` is null, or the card's MUN Shape record (below); a change of it
alone is published as `{"type":"shape","slot","insertion","shape"}`, so it
never resends the cover. Consumers that do not know Shape ignore both.

`insertion` is a random token minted for that one insertion. Slots restart at
`c1` with the service and the serial is the card's own claim, so a consumer
that must know whether the card present now is the card it saw earlier (the
launcher, for a running session) compares insertions; a stage request that
names another insertion is refused with `card_mismatch`.

Framing: one line never exceeds `MAX_FRAME_BYTES` (2 MiB): a 1 MiB cover is
~1.37 MiB of base64 plus metadata, and consumers (shell, launcher) accept at
least that per line and reconnect on anything longer. Should a frame ever
exceed the budget, the state is sent with `cover_omitted` instead of the
picture. Writes are queued per client: a peer that has not drained its socket
yet keeps its bytes queued until the socket is writable again, and only a
peer that leaves more than 8 MiB unread is dropped. A partial write therefore
never disconnects a slow shell or launcher. The service keeps at most 8
connections (its readers are the shell, the launcher and the console reset)
and at most 16 MiB unread by all of them together: past that, the client
furthest behind is dropped first (the newest of equals), and a connection
past the eighth replaces a client that leaves bytes unread, in the service
or, on Linux, in the kernel's buffers (a client that never reads a small
snapshot holds nothing in the service), or is refused when every client has
read everything. Text that UTF-8 cannot carry (a
lone surrogate in a card's file) crosses the socket, and reaches the journal,
as `?`.

## MUN Shape export

The package contract is [docs/shape.md](../../docs/shape.md); this is what
the service does with it.

- When a card becomes `valid` (after that state is published), a worker
  thread checks its `<content.root>/mun-shape/` with `mun_card.shape`,
  copying each file the checker reads once from the card, in 256 KiB
  chunks, into `/run/mun/shape/.<insertion>.<attempt>.part/` (0700), and
  validating those bytes. It keeps only the accepted files, writes the
  normalised `shape.json` (with `insertion` and `version`), seals files
  0440, folders 0550 and the export's own folder 0750 (its owner's write
  permission lets every system rename it) with the group `mun-shell`, and,
  as its last step,
  renames the folder to `/run/mun/shape/<insertion>.<attempt>/`. Card files
  are opened one component at a time with `O_NOFOLLOW` and non-blocking;
  the event loop never reads the card for this. `<attempt>` counts copies
  within the service's lifetime: a copy started again for the same
  insertion (after a failed release) has names of its own, so the earlier
  attempt's cleanup, which runs in the background, never touches it.
- Record: `shape = {state, insertion, version, notes, path?, files?, bytes?}`
  with `state` ∈ `preparing | ready | partial | unused | none`, `notes`
  the checker's (`code, level, block, where, detail`, at most 32, with
  `notes_omitted`), `path`, `files` and `bytes` when an export exists.
  A failure of the export itself is `unused` with `shape_export_failed`.
- Cancellation (release, removal, replacement, service stop) is checked
  before every entry and between chunks, together with the card's
  generation. A result for an insertion that is no longer current, or that
  was cancelled, is deleted, never published. One copy runs at a time; a card
  that becomes valid meanwhile waits in `preparing`.
- `release` cancels the copy, then proceeds by one path, taken again when
  a save in flight ends and when the copy ends, in either order: the card is
  unmounted only when no save is in flight and no copy still reads it.
  Waiting for a save has no deadline (it ends and answers); waiting for the
  copy has one, `MUN_CARDD_SHAPE_RELEASE_WAIT` seconds (3), armed once per
  release, without blocking the loop. Past it the caller is answered
  `released {ok: false, error: card_busy}` and the release stays pending
  (no saves, stages or copies); when both have ended the card is unmounted
  strictly and published as `released`, or, if the unmount fails, is usable
  again as after any failed release and its copy starts again. The caller
  is answered once. A card pulled while its release waits is answered
  `card_removed`, and what ends later leaves it alone.
- The export is deleted on release, removal and replacement; everything under
  `/run/mun/shape/` is deleted at start and at stop.
- Hooks: `MUN_CARDD_SHAPE_ROOT` (default `/run/mun/shape`), and
  `MUN_CARDD_SHAPE_DELAY` (seconds, unset in the unit), a pause before each
  chunk that ends at once on cancellation, for removal and release
  experiments.

## Content grant for mount-access cards

When a staged card declares `content.access = "mount"`, the `staged` reply
also carries `content: {"device": "/dev/vdX", "root": "<content.root>"}`.
The service does not mount anything extra: the launcher has systemd mount
that device read-only inside the game unit ([content during play](../../docs/runtime.md#content-during-play)). The service's own
mount stays private; both are the same superblock, so a save's read-write
window on this side never makes the game's view writable.

## Saves

Cards mount `ro,nosuid,nodev,noexec,errors=remount-ro` with the journal
loaded: a read-only mount writes nothing (the image hash is unchanged after
mount and unmount), and a card that needs recovery is still rejected before
mounting, so nothing is ever replayed on its own. The `noload` option of
earlier versions is gone because a filesystem mounted that way cannot be
remounted read-write with journaling, and a save must be journaled.

The error policy is the console's, never the card's: without `errors=` ext4
takes it from the card's superblock, where `panic` stops the kernel at the
first damaged inode it reads, on a read-only mount too. Every mount and
remount names `errors=remount-ro` together with `nosuid,nodev,noexec`, so
what holds never depends on which options a remount keeps. In a save's
read-write window it stops every write at the first inconsistency: the save
fails and says so. A card that asks for `panic` is not refused; `mun-card
inspect` reports it.

The root-only control socket accepts, besides `stage`:

- `save {slot, insertion, serial, version, game, session, schema, payload}`:
  after checking that the active card is exactly that insertion, serial,
  version and game, and that `payload` is an object of at most 64 KiB,
  measured as compact JSON in UTF-8, with a positive integer `schema`
  (text UTF-8 cannot carry, a lone surrogate, is `save_invalid`; an
  envelope that would take more than 112 KiB on the card is
  `save_too_large` before anything is written), the service remounts the
  card `rw` and writes
  through a directory descriptor for `<saves>/<game>` obtained one component
  at a time with `O_NOFOLLOW` (a link anywhere is `save_path_unsafe`; a
  directory missing on the way, the saves location included, is made by name
  in its parent's descriptor): it
  removes any object left under `save.json.tmp` by name, creates the
  temporary exclusively, `fsync`s it, gives the current `save.json` a second
  name `save.json.prev` with a hard link (a current file that is not an
  envelope is moved aside as `save.json.damaged-<time>` instead), renames the
  temporary onto `save.json` in one operation, `fsync`s the directory and
  remounts `ro`, which flushes the journal and marks the filesystem clean.
  Only then it replies `saved {ok: true, bytes, sha256}`. Errors:
  `card_unavailable`, `card_mismatch`, `save_invalid`, `save_too_large`,
  `save_path_unsafe`, `save_busy` (one write at a time), `no_space`,
  `sync_failed`, `write_failed`, `card_removed`. On any error the temporary
  file is removed and the slot still holds a loadable save (the old one until
  the final rename); the card is remounted read-only whatever happened.
  The kernel refuses that remount (`EBUSY`) while a file removed from the
  card is still open, and a save removes the previous copy it replaces; a
  game with mount access can hold that file open through its own mount of
  the same device. The save is then answered `sync_failed` ("La partida se
  escribió, pero la tarjeta no pudo cerrarse; no la retires todavía"), and
  the remount is owed: the service tries it again every 5 s and before a
  release, never under a save in flight, and logs "is read-only again" when
  it succeeds.
- `stage` also copies the current `save.json` (up to the bound, 112 KiB for a
  single-object save, one byte over
  so an oversized file reads as damaged) into `<dest>/work/save.json` owned by
  the game user and readable by it alone (0600), created exclusively through
  a descriptor of that `work/` directory opened without following a link
  (a name already there is removed, never opened), and reports
  `save: {present, copied, bytes, recovered}`. The
  copy is read through the same link-safe walk; only a regular file counts,
  and the type is checked by name before a non-blocking open, so a FIFO or a
  device under a save name is answered as absent instead of hanging the
  service.
  If `save.json` is missing while `save.json.prev` is a valid envelope, the
  previous save is handed out with `recovered_from` set and `recovered: true`.
- `release {serial}`: safe removal. No new writes are accepted, a write in
  flight completes, the card is unmounted strictly (a plain `umount` that
  must succeed) and published as `released` ("Puedes retirar la Game Card"),
  then the reply says so. If the unmount fails the reply is
  `released {ok: false, error: unmount_failed}`, the card stays mounted and
  usable, and the host must not unplug; the request may be retried. Only a
  device that already vanished is cleaned up with a lazy detach. A card that
  a save left writable is closed first; while that remount is still refused
  the reply is `released {ok: false, error: card_busy}`, as it is when the
  MUN Shape copy has not closed its files in time, and the card is not
  unmounted.

A line that is not JSON, is not a JSON object or names no known request is
answered `error {code: bad_request}`, and so is a line still unfinished
after 112 KiB (the bound of a single-object envelope, which admits every
save within its limit), which also closes the connection. A request whose handling fails unexpectedly is
answered in its own reply type with `internal_error` (a `stage` or a `save`
that fails so once its work has begun, with `stage_failed` or
`write_failed`), and the service goes on.

- Directory saves: `save {…, payload_kind: "files", payload_file,
  payload_size, payload_sha256}` for a card that declares `saves.directory`.
  The payload is not on the line: `payload_file` must lie under
  `/run/mun/launch/`, without `/.`, be a regular root-owned file opened
  with `O_NOFOLLOW|O_NONBLOCK`, of exactly `payload_size` bytes and that
  SHA-256; its files must be single safe names whose data match their own
  size and hash, at most 64 files and `saves.max_bytes` in total. The bounds
  come from the card: payload `4/3 × max_bytes + 64 KiB`, envelope file
  8 KiB more (without `saves.directory`, 64 KiB and 112 KiB). The envelope
  carries `payload_kind: "files"`. For such a card a single-object save is
  `save_invalid`, an envelope whose files do not match is damaged (never
  kept as `save.json.prev`, moved aside on the next write), and `stage`
  hands out `save.json.prev` as recovered when `save.json` is not intact, or
  `{present: true, copied: false, error: save_damaged}` when neither is.

`MUN_CARDD_SAVE_DELAY` (seconds, unset in the unit) is a test hook that
widens the write window for removal experiments.

## Privileges

Root for `mount`, with `CapabilityBoundingSet` limited (`CAP_SYS_ADMIN CAP_CHOWN
CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER`; the override is what lets it
write under a `saves/` directory owned by whoever built the card), `ProtectSystem=strict`,
writes only under `/run/mun`, device access only to virtio block devices,
`NoNewPrivileges`, no network address families. Test hook
`MUN_CARDD_VALIDATION_DELAY` (seconds) is off unless set in the unit.

## Install

A MUN OS image build (`os/`) runs `deploy/stage.sh SRC CARD_PACKAGE DESTDIR`
into the image and takes the service's runtime packages from
`deploy/packages` (mirrored in `os/mkosi/mkosi.conf`) and its group from
`deploy/sysusers.conf`.

## Operate

```sh
./mun vm cardd-log        # journal
./mun vm run -- systemctl status mun-cardd
./mun vm run -- python3 /opt/mun/launchd/launchd.py cards   # the card records, as the host tools read them
```

Recovery: `systemctl restart mun-cardd`; the shell reconnects on its
own. Mount points survive a service restart only through re-detection, which
the rescan performs at start. The unit restarts the service itself however
it ends (`Restart=always`, 2 s apart) and has no start limit: a service that
ended quickly a few times would otherwise stay failed until the next boot,
and with it the console's only reader.
