# MUN OS

[English](README.md) · **Español**

MUN™ es una consola de videojuegos que construimos en abierto, para juegos
que posees en Game Cards. MUN OS es su sistema operativo, de código abierto y
desarrollado en público. Ya puedes ejecutarlo en tu ordenador como consola
virtual, probarlo, desmontarlo y participar; el hardware oficial de la consola
llegará más adelante.

![La pantalla principal de MUN Shell con la Game Card de MUN Collect insertada](docs/images/mun-shell-home.jpg)

## Lo que puedes hacer hoy

1. **Probar MUN OS, sin compilar.** Descarga las herramientas y una imagen
   ya hecha, arranca la consola virtual, inserta una Game Card, juega,
   guarda, retírala con seguridad y continúa después:
   [primeros pasos](docs/es/getting-started.md). Necesitas Python y QEMU.
2. **Crear para MUN.** Pon un juego en una Game Card y viste la consola con
   su identidad gracias a MUN Shape:
   [crear una Game Card](docs/es/guides/create-game-card.md),
   [traer un juego a MUN](docs/es/guides/port-a-game.md) y
   [vestir la consola con tu juego](docs/es/guides/shape-your-game.md). Las
   herramientas de tarjetas necesitan además e2fsprogs.
3. **Construir y adaptar el sistema.** Construye tú la imagen a partir de
   entradas con versión fijada, cámbiala y ejecuta la tuya:
   [construir la imagen tú mismo](docs/es/getting-started.md#construir-la-imagen-tú-mismo),
   [construcción de la imagen](os/README.md) y
   [arquitectura](docs/architecture.md), estas dos en inglés. La
   construcción está comprobada en un Mac con Apple Silicon.
4. **Contribuir y seguir el desarrollo.** Pruébalo en tu ordenador, avisa de
   lo que falla o confunde, traduce, haz paquetes y juegos de ejemplo,
   trabaja en los servicios y las herramientas:
   [contribuir](CONTRIBUTING.es.md#cómo-ayudar). Las
   [versiones](https://github.com/Play-MUN/mun-os/releases) y las
   [pull requests](https://github.com/Play-MUN/mun-os/pulls) muestran hacia
   dónde va.

Jugar, hacer tarjetas y construir tienen cada uno sus requisitos:
[compatibilidad](docs/es/compatibility.md).

## BOP: Buy. Own. Play. (Compra. Posee. Juega.)

Una Game Card legítima basta para poseer su juego y jugarlo. En la consola
virtual, con imágenes de disco en lugar de tarjetas:

- **Insértala y juega.** El juego arranca desde la tarjeta, sin cuenta, sin
  activación y sin red.
- **Tu progreso viaja con la tarjeta.** Las partidas se escriben en la
  tarjeta, no en la consola: otra consola, u otra build de MUN OS, continúa
  desde ella.
- **Retírala con seguridad,** y la tarjeta sale de la ranura con todo lo que
  lleva.
- **La consola no guarda una biblioteca.** Su almacenamiento es para el
  sistema; *Mis juegos* lo dice claro: tus juegos viven en sus Game Cards.

## Lo que funciona hoy, y lo que todavía no

MUN OS v0.1.0-dev.3 es una versión preliminar de desarrollo. En la consola
virtual:

- **MUN Shell**, la interfaz propia de la consola, arranca sola: la pantalla
  principal, Configuración (inglés o español, de 720p a 1440p, sonidos),
  menús con sonido.
- **Las Game Cards** se validan antes de que se ejecute nada de ellas. Un
  juego arranca aislado y devuelve la consola acabe como acabe, sus partidas
  van a la tarjeta y una tarjeta se retira con seguridad. Los juegos hechos
  con SDL u OpenGL arrancan en la resolución de la consola.
- **MUN Shape v1:** un paquete declarativo de recursos en la tarjeta (una
  paleta, materiales, el objeto de la tarjeta, un mundo detrás de los menús,
  transiciones y sonidos) que la consola dibuja con su propio código mientras
  la tarjeta está dentro; nada de la tarjeta se ejecuta. MUN conserva la
  disposición, los textos, Configuración y la legibilidad, y el jugador decide
  cuánto se muestra.
- **Ejemplos:** MUN Collect, un juego pequeño; una prueba de gráficos y
  audio; dos paquetes de MUN Shape.

| MUN Collect, sin paquete | La misma tarjeta con el ejemplo `sea` |
| --- | --- |
| ![La pantalla principal con la Game Card de MUN Collect, con el aspecto de MUN y los colores leídos de su portada](docs/images/mun-shape-before.jpg) | ![La misma pantalla con MUN Collect llevando el ejemplo sea: un mundo bajo el agua, placas de cristal y el objeto de la tarjeta mostrando el mar](docs/images/mun-shape-after.jpg) |

**Todavía no:** mandos, actualizaciones de tarjetas y del sistema, ni ningún
hardware físico. La [arquitectura](docs/architecture.md) (en inglés) dice qué
existe y qué está previsto.

## Abierto, y hacia dónde va

La obra propia de MUN OS tiene licencia [Apache License 2.0](LICENSE): puedes
estudiar el código, cambiarlo, reconstruir la imagen y distribuirla según
los términos de sus licencias. Cada versión publica el código fuente
correspondiente de todos los paquetes de su imagen; los componentes de
terceros conservan sus propios términos, y los nombres y logotipos de MUN y
Play MUN tienen [los suyos](NAME-AND-LOGO.txt)
([licencias](docs/licensing.md), en inglés).

Hoy hay una imagen experimental, ARM64 como lo será la consola, que QEMU
ejecuta como consola virtual; la propia imagen lo dice
(`environment = "qemu-arm64"`, `release = false`). MUN -1, la primera consola,
tendrá **una única configuración de hardware con soporte oficial**, todavía
sin elegir; la consola montada y las versiones DIY sobre esa configuración
usarán la misma imagen oficial. Abierto y adaptable no significa que hoy
funcione en cualquier placa: un port físico necesita su propia integración
(arranque, pantalla, entrada, almacenamiento, el lector de tarjetas), y los
ports a otro hardware son bienvenidos como forks mantenidos por separado.

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
| macOS, Apple Silicon | Virtualizado (HVF) | La v0.1.0-dev.3 en un Mac (macOS 27), con ventana, teclado y sonido; la v0.1.0-dev.2 en CI en macOS 15, sin pantalla y emulado (no hay HVF dentro de una VM) |
| Linux, x86_64 o ARM64 | Emulado; KVM en ARM64 aún sin probar | La v0.1.0-dev.2 en CI y en máquinas virtuales Ubuntu 24.04, sin pantalla |
| Windows, x86_64 o ARM64 | Emulado | La v0.1.0-dev.2 en CI (Windows Server 2025; Windows 11 ARM64), sin pantalla |

«En CI» es el flujo de trabajo Hosts: descargar una versión preliminar,
arrancar la consola, jugar su tarjeta y apagar, en las máquinas virtuales de
GitHub, sin ventana. Comprueba cada versión preliminar cuando se publica, y
las notas de esa versión dan el resultado: la v0.1.0-dev.3 está pendiente ahí
hasta su propia ejecución. Todavía no se ha probado en ningún ordenador con
Linux o Windows, ni con ventana, teclado y sonido fuera de macOS.

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
- [Vestir la consola con tu juego](docs/es/guides/shape-your-game.md) con MUN
  Shape, y [verlo en acción](docs/es/guides/see-shape-in-action.md)
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
| `examples/` | Juegos de ejemplo y paquetes de MUN Shape |
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
