# MUN OS

[English](README.md) · **Español**

**BOP — Buy. Own. Play.** (Compra. Posee. Juega.) MUN™ es una consola de
videojuegos en desarrollo, para juegos que posees en Game Cards: insertas una
tarjeta, juegas sin conexión y tu progreso viaja con la tarjeta. MUN OS es su
sistema operativo de código abierto.

![La pantalla principal de MUN Shell con la Game Card de MUN Collect insertada](docs/images/mun-shell-home.jpg)

MUN OS es **experimental**. Todavía no hay hardware de MUN ni ninguna versión
con soporte. Lo que existe es una imagen de desarrollo, ARM64 como lo será la
consola, que QEMU ejecuta como consola virtual en ordenadores con macOS, Linux
y Windows; la propia imagen lo dice (`environment = "qemu-arm64"`,
`release = false`).

MUN -1, la primera consola, tendrá **una sola configuración de hardware con
soporte oficial**, aún no elegida; la consola montada y las construcciones
caseras sobre esa configuración usarán la misma imagen oficial. Las
adaptaciones a otro hardware son bienvenidas como bifurcaciones (forks) que
se mantienen por su cuenta.

## Lo que funciona hoy

En la consola virtual: MUN Shell arranca solo; las Game Cards (imágenes de
disco que hacen de tarjetas) se validan y se muestran; un juego de una
tarjeta arranca aislado y devuelve la consola acabe como acabe; las partidas
se escriben en la tarjeta y se recuperan en otra consola o en otra build; una
tarjeta se retira con seguridad; la resolución (720p, 1080p, 1440p) se aplica
al momento, y los juegos hechos con SDL u OpenGL arrancan en ella; los menús
tienen sonido; inglés y español. Mientras su tarjeta está dentro, un juego
puede vestir la consola con su propia identidad, MUN Shape v1: colores,
materiales, el objeto de la tarjeta, un mundo detrás de los menús,
transiciones y sonidos, leídos de una carpeta de la tarjeta y dibujados por
MUN. Los ejemplos son MUN Collect, un juego pequeño, una prueba de gráficos y
audio, y dos paquetes de MUN Shape.

Todavía no: mandos, actualizaciones de las tarjetas y del sistema, y
cualquier hardware físico. [Arquitectura](docs/architecture.md) (en inglés)
explica qué existe y qué está previsto.

## Por dónde empezar

- **Prueba la versión preliminar.** Descarga las herramientas y la imagen de
  una [versión preliminar](https://github.com/Play-MUN/mun-os/releases),
  arranca la consola y juega la Game Card que la acompaña; no hace falta
  compilar nada. [Primeros pasos](docs/es/getting-started.md) tiene los pasos
  para cada ordenador.
- **Haz una Game Card.** Pon un juego en una tarjeta, juégalo, guarda en ella,
  retírala y continúa, con MUN Collect como ejemplo:
  [crear una Game Card](docs/es/guides/create-game-card.md) y después
  [traer un juego a MUN](docs/es/guides/port-a-game.md).
- **Construye la imagen.** Componla tú mismo a partir de entradas con versión
  fijada, en un Mac con Apple Silicon:
  [construir la imagen tú mismo](docs/es/getting-started.md#construir-la-imagen-tú-mismo)
  y [construcción de la imagen](os/README.md) (en inglés).
- **Contribuye.** Ejecuta las comprobaciones, elige un issue y abre un pull
  request: [contribuir](CONTRIBUTING.es.md).

## Dónde funciona

Todos los ordenadores de abajo ejecutan la misma imagen con las mismas
herramientas; solo cambia cómo se instala QEMU
([primeros pasos](docs/es/getting-started.md#1-qué-necesitas)). QEMU ejecuta
la consola con la virtualización del propio procesador en un ordenador ARM64
cuyo sistema se la ofrece a QEMU (macOS en Apple Silicon, Linux con KVM); si
no, tanto en x86_64 como en ARM64, emula el procesador: la misma consola, más
lenta.

| Tu ordenador | El procesador de la consola | Comprobado |
| --- | --- | --- |
| macOS, Apple Silicon | Virtualizado (HVF) | En un Mac (macOS 27), con ventana y sonido; en CI en macOS 15, sin pantalla y emulado (no hay HVF dentro de una VM) |
| Linux, x86_64 o ARM64 | Emulado; KVM en ARM64 aún sin probar | En CI y en máquinas virtuales Ubuntu 24.04, sin pantalla |
| Windows, x86_64 o ARM64 | Emulado | En CI (Windows Server 2025; Windows 11 ARM64), sin pantalla |

«En CI» es el flujo de trabajo Hosts: descargar una versión preliminar,
arrancar la consola, jugar su tarjeta y apagar, en las máquinas virtuales de
GitHub. Todavía no se ha probado en ningún ordenador con Linux o Windows.
Jugar, hacer tarjetas y construir imágenes tienen cada uno sus requisitos:
[compatibilidad](docs/es/compatibility.md).

## Principios

- Una Game Card legítima puede arrancar su juego, si funciona sin conexión,
  sin cuenta ni servidor de activación. Las funciones en línea opcionales
  tienen que ser explícitas.
- Las partidas viven en la tarjeta y viajan entre consolas.
- El almacenamiento interno de la consola es para el sistema, la
  recuperación, cachés acotadas y espacio temporal, no para instalar la
  biblioteca del jugador.
- Las actualizaciones, cuando existan, mantendrán disponible la versión
  original de un juego.
- El software común depende de los contratos de MUN y de Linux; el arranque,
  la GPU, la NPU, el lector y los detalles térmicos pertenecen a la
  integración del hardware.
- Los datos de juego que aporta un jugador están separados del software de
  MUN: MUN OS no lleva ningún juego salvo sus propios ejemplos.

## Documentación

- [Primeros pasos](docs/es/getting-started.md) y
  [compatibilidad](docs/es/compatibility.md)
- [Crear una Game Card](docs/es/guides/create-game-card.md) y
  [traer un juego a MUN](docs/es/guides/port-a-game.md)
- [Vestir la consola con tu juego](docs/guides/shape-your-game.md) con MUN
  Shape, y [verlo en acción](docs/guides/see-shape-in-action.md) (en inglés)
- Para autores de tarjetas y juegos: [Game Cards](docs/game-cards.md),
  [partidas](docs/saves.md), [ejecutar un juego](docs/runtime.md),
  [MUN Shape](docs/shape.md) (en inglés)
- [Arquitectura](docs/architecture.md), [construcción de la imagen](os/README.md),
  [laboratorio](vm/README.md) (en inglés) y [todos los documentos](docs/es/README.md)
- [Licencias](docs/licensing.md) (en inglés), [seguridad](SECURITY.es.md),
  [contribuir](CONTRIBUTING.es.md)

## Mapa del repositorio

| Ubicación | Responsabilidad |
| --- | --- |
| `services/` | MUN Shell, el servicio de tarjetas y el lanzador |
| `tools/` | `mun-card` y el paquete compartido de tarjetas |
| `os/` | La composición de la imagen de MUN OS: entradas con versión fijada, configuración de mkosi, scripts del constructor |
| `vm/` | El laboratorio: VM constructoras, invitados de imagen, tarjetas virtuales, descargas |
| `mun` | El punto de entrada de las herramientas: `./mun get`, `./mun play`, `./mun card`, `./mun dev` |
| `examples/` | Juegos de ejemplo |
| `tests/`, `scripts/` | Las regresiones del anfitrión y la comprobación de la documentación |
| `docs/` | Guías, contratos, arquitectura y referencia; las traducciones al español, en `docs/es/` |
| `.local/` | Ignorado: builds, invitados, imágenes de tarjeta, descargas |

`make check` (Python 3.9 o posterior, Make) ejecuta la comprobación de la
documentación, la de sintaxis de Python, las regresiones del anfitrión y, con
un compilador de C, las pruebas de partidas del juego de ejemplo: sin red ni
VM.

## Quién lo hace, y la licencia

MUN lo hace **Play MUN**, el estudio de **Iván Moreno Mendoza**, que fundó el
proyecto y mantiene MUN OS: revisa los cambios y decide qué entra en su
versión oficial. La obra propia de MUN OS es Copyright 2026 Iván Moreno
Mendoza, con licencia [Apache License 2.0](LICENSE) ([NOTICE](NOTICE)); las
contribuciones siguen siendo de sus autores y se licencian en los mismos
términos. Los componentes de terceros (tipografías, paquetes de Debian)
conservan sus propios términos, y la Apache License no concede derechos sobre
los nombres y logotipos de MUN y Play MUN, que tienen
[sus propios términos](NAME-AND-LOGO.txt):
[licencias](docs/licensing.md) (en inglés). MUN™ es una marca de Iván Moreno
Mendoza.

Los textos de las licencias (`LICENSE`, `NOTICE`, `NAME-AND-LOGO.txt`) están
en inglés y son los que valen; este resumen no los sustituye.

El proyecto tuvo antes otro nombre. Las Game Cards que se hicieron entonces
llevan `neptune.toml`, guardan como `neptune-save/1` y dan a sus juegos
variables `NEPTUNE_*`; la consola todavía las lee y las juega, y
`mun-card convert` hace una copia MUN si se le pide
([generaciones de nombres](docs/game-cards.md#naming-generations), en inglés).
