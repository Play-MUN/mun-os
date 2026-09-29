# Languages and native boundaries

## What the components use

| Component | Language | Baseline |
| --- | --- | --- |
| MUN Shell | C++20 with Qt 6 QML | Debian 13's Qt 6.8 and GCC 14, built with CMake and Ninja |
| Card service, launcher, card tool, host tools | Python 3; the standard library only, except the card service's pyudev | 3.13 in the image; the shared `mun_card` package and the host tools also run on macOS's Python 3.9 |
| Example games | C11 | GCC 14 in the image build; `mun-gl-probe` against SDL2 and OpenAL Soft |
| Image build and service staging | POSIX shell, mkosi | the pinned builder of `os/inputs.json` |

## Choosing a language for a new component

No language is mandatory. Rust, C, C++, Python and shell are all valid when
the component's job justifies them: safe handling of untrusted data,
integration with native libraries, fast iteration on host automation, or
short command orchestration. Neither language guarantees higher performance:
profile representative workloads before adding FFI or rewriting a working
component.

For a substantial new component, record its job, target, candidate
libraries, language alternatives, toolchain and runtime baseline,
dependencies, build and test commands, and why the chosen option is simpler
over its expected lifetime. Pin the toolchain and dependencies when they are
introduced.

- Executable services live in `services/` regardless of language.
- Keep a component's tests near it where useful; cross-process and host-side
  behaviour belongs in `tests/`.

## Boundaries and coding guidance

Rust: typed errors at reusable boundaries, useful application context,
documented unsafe invariants, and no panic for malformed media or expected
I/O failures.

C++: RAII and explicit ownership, bounded views with documented lifetimes, a
consistent error policy per component, and sanitizers where supported.

C interfaces: fixed-width values and opaque handles where appropriate; define
buffer lengths, ownership, release functions, status codes and ABI
versioning. Keep exceptions, Rust panics, STL containers and Rust-owned
layouts behind the ABI; never let a panic or a C++ exception cross it.
Document ownership, lifetime, error and thread contracts at every native
boundary.

Prefer a versioned local process protocol for independent services, as the
console services do (UNIX sockets, newline-delimited JSON). Use in-process
FFI when latency, library integration or deployment actually warrants it.
