# Compatibilidad

[English](../compatibility.md) · **Español**

En tu ordenador corren tres cosas distintas, con necesidades distintas: jugar
con una imagen descargada, hacer Game Cards y construir una imagen.
«Comprobado» dice abajo cómo y dónde; también se dice qué no se ha probado.

## Jugar con una imagen descargada

`./mun get` y `./mun play` (o `./mun dev run`): Python 3.9 o posterior y QEMU
8.2 o posterior con su firmware UEFI de ARM64
([primeros pasos](getting-started.md#1-qué-necesitas)).

| Ordenador | Procesador de la consola | Sin pantalla (descarga, arranque, tarjeta, juego, apagado) | Ventana, teclado, sonido |
| --- | --- | --- | --- |
| macOS 27, Apple Silicon | Virtualizado (HVF) | Comprobado en un Mac (v0.1.0-dev.5, con MUN Collect y con la tarjeta vestida con Reef, cuyo movimiento debe reproducir el shell; v0.1.0-dev.4) | La v0.1.0-dev.5: su sonido grabado desde una consola sin pantalla; ventana, teclado y sonido (Cocoa, CoreAudio) con una construcción anterior de la versión preliminar. Comprobado en un Mac con la v0.1.0-dev.4 |
| macOS 15, ARM64 (la máquina virtual de GitHub) | Emulado: no hay HVF dentro de una VM | La v0.1.0-dev.5, pendiente; comprobado en CI (v0.1.0-dev.4) | Sin probar |
| Linux x86_64, Ubuntu 24.04 | Emulado | La v0.1.0-dev.5, pendiente; comprobado en CI (v0.1.0-dev.4) y en una máquina virtual (v0.1.0-dev.2) | Sin probar (GTK o SDL) |
| Linux ARM64, Ubuntu 24.04 | Emulado (sin `/dev/kvm` en ninguno de los dos) | La v0.1.0-dev.5, pendiente; comprobado en CI (v0.1.0-dev.4) y en una máquina virtual (v0.1.0-dev.2) | Sin probar |
| Linux ARM64 con KVM | Virtualizado (KVM) | Sin probar | Sin probar |
| Windows Server 2025, x86_64 | Emulado, QEMU para Windows | La v0.1.0-dev.5, pendiente; comprobado en CI (v0.1.0-dev.4) | Sin probar |
| Windows 11, ARM64 | Emulado, el QEMU ARM64 de MSYS2 | La v0.1.0-dev.5, pendiente; comprobado en CI (v0.1.0-dev.4) | Sin probar |

«Comprobado en CI» es el flujo de trabajo Hosts del repositorio
(`.github/workflows/hosts.yml`) en las máquinas virtuales de GitHub:
descarga una versión preliminar como lo haría un jugador, arranca la consola,
inserta MUN Collect, lo juega y sale, retira la tarjeta y apaga. Se ejecuta
sobre cada versión preliminar cuando se publica, y las notas de esa versión
dan el resultado: la v0.1.0-dev.4 pasó en los cinco
([su ejecución](https://github.com/Play-MUN/mun-os/actions/runs/38839513795));
la v0.1.0-dev.5 queda pendiente hasta que el flujo se ejecute sobre ella.
Dice que las herramientas y la imagen funcionan en
ese sistema; no mide la velocidad, y no se ha probado ningún ordenador físico
con Linux o Windows. Las consolas
emuladas son varias veces más lentas que las virtualizadas.

Dentro de la consola, los juegos arrancan en la resolución de la consola si
usan SDL u OpenGL, y los de framebuffer, al tamaño que tenía la pantalla al
arrancar ([ejecutar un juego](../runtime.md#display), en inglés).

La consola virtual dibuja con el renderizador por software de Qt Quick y
tiene teclado y puntero. Los mandos manejan los menús de MUN Shell
([MUN Shell, Controls](../../services/mun-shell/README.md#controls), en
inglés), lo que se ha probado solo en la placa de desarrollo (abajo): a una
consola virtual no se le ha pasado ninguno.

## Hacer Game Cards

`./mun card create`, `inspect`, `hash` y `convert` construyen y leen imágenes
de tarjeta en el anfitrión sin montarlas. Necesitan Python y e2fsprogs 1.47 o
posterior (`mke2fs`, `debugfs`).

| Ordenador | Estado |
| --- | --- |
| macOS, con `brew install e2fsprogs` | Comprobado |
| Linux, con el e2fsprogs de la distribución | Debería funcionar; las comprobaciones de las pull requests lo usan en Ubuntu |
| Windows | Sin soporte directo; usa WSL 2 (sin probar) |

Una versión preliminar ya trae sus tarjetas: para jugar no hace falta nada de
esto.

## Construir una imagen

`./mun dev build` compone la imagen con mkosi dentro de una VM constructora
desechable, a partir de entradas con versión fijada, y necesita Git, Make,
OpenSSH, QEMU, e2fsprogs, unos 10 GB de disco y red durante la construcción.

| Ordenador | Estado |
| --- | --- |
| macOS, Apple Silicon (HVF, `hdiutil` para la semilla del constructor) | Comprobado: la build de la v0.1.0-dev.4 tardó unos 10 minutos, con sus entradas ya descargadas |
| Linux ARM64 con KVM (`xorriso` para la semilla) | Las herramientas lo admiten; sin probar |
| Linux x86_64, o ARM64 sin KVM | El constructor se emula: horas; sin probar |
| Windows | Sin soporte; WSL 2 sin probar |

## Hardware

Ningún hardware tiene soporte. MUN™ -1 tendrá una sola configuración de
hardware con soporte oficial; se diseña alrededor de la Radxa Dragon Q8B,
en la que MUN OS no se ha ejecutado.

MUN OS sí funciona en **MUN Dev**, nuestra placa de desarrollo, una Radxa
Dragon Q6A, con un perfil experimental que construyes tú (no se publica
ninguna imagen): 1080p a 60 Hz por HDMI con su GPU, sonido, Game Cards en
tarjetas microSD. [MUN Dev](mun-dev.md#qué-se-ha-comprobado) dice qué se
comprobó allí, con qué construcción, y qué queda pendiente. Es una
plataforma de desarrollo: nada de lo comprobado en ella certifica el
hardware de la consola.
