# Console services

The console processes of MUN OS, each a systemd service in the image:

- [`mun-shell`](mun-shell/README.md): the Qt/QML interface with a C++ host.
- [`mun-cardd`](mun-cardd/README.md): card detection, validation, mounts,
  entry staging and save writes, in Python.
- [`mun-launchd`](mun-launchd/README.md): game sessions, save coordination
  and results, in Python.

Each component documents its units, local protocols and privileges. Each
`deploy/` directory holds what the image build uses: `stage.sh` installs the
component's files under a root directory, `packages` lists its runtime
packages (mirrored in `os/mkosi/mkosi.conf` and checked by
`tests/test_os.py`), and `sysusers.conf` declares its users and groups.
How they fit together: [docs/architecture.md](../docs/architecture.md).
