# Tests

`make test` runs the host-side regressions with Python's standard library;
`make check` runs them after the documentation link check
(`scripts/check_foundation.py`) and the Python syntax check, and adds MUN
Collect's C save tests (`examples/mun-collect/tests`) when a C compiler is
present. No network, QEMU installation, root or VM boot is needed: the tests
use temporary directories and fake process, protocol and device boundaries.

| File | Covers |
| --- | --- |
| `test_card.py` | Manifest validation, both naming generations, image creation and variants, offline inspection, conversion |
| `test_shape.py` | MUN Shape packages: the strict JSON profile, schema, file headers, budgets, contrast proofs and transition plans, the cover palette, the template, every defective package, the two samples, a card image read like its folder |
| `test_cardd.py` | The card service: eligibility, states, framing, staging, single-object saves and their failures, safe release |
| `test_cardd_shape.py` | The card service's MUN Shape export: after `valid`, whole or nothing, links and FIFOs, Play and saves during a copy, safe release within a chunk or pending past a stuck read, removal, replacement and late completions, start/stop cleanup |
| `test_directory_saves.py` | Directory saves: checks, capture rule, carried units, restore, adoption, failure cases |
| `test_launchd.py` | The launcher: launch flow, identity, runtime profiles, environment, results, cleanup, recovery |
| `test_launch_cleanup.py` | The cleanup helper and unit |
| `test_deploy.py` | The services' staging scripts, run into temporary roots |
| `test_os.py` | The image composition, pinned inputs, BUILD-INFO and the `./mun` entry point |
| `test_mundev.py` | Builds, the builder and image guests of `./mun dev` |
| `test_vm.py` | The laboratory tool: QMP and qemu-ga framing, card attach/detach, the attach registry, lifecycle locks, release reconciliation, audio, downloads |

A few tests need Linux (inotify, file leases, `/proc`) or Python 3.11's
`tomllib` and are skipped where those are missing. Behaviour that only a running guest shows (display, input, a real card
mount, a real game) is checked with the laboratory ([vm/README.md](../vm/README.md)).
A virtual interruption does not establish what a physical medium does on
power loss.
