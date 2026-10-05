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
| macOS 27, Apple Silicon | Virtualizado (HVF) | Comprobado en un Mac | Comprobado en un Mac (Cocoa, CoreAudio) |
| macOS 15, ARM64 (la máquina virtual de GitHub) | Emulado: no hay HVF dentro de una VM | Comprobado en CI | Sin probar |
| Linux x86_64, Ubuntu 24.04 | Emulado | Comprobado en CI y en una máquina virtual | Sin probar (GTK o SDL) |
| Linux ARM64, Ubuntu 24.04 | Emulado (sin `/dev/kvm` en ninguno de los dos) | Comprobado en CI y en una máquina virtual | Sin probar |
| Linux ARM64 con KVM | Virtualizado (KVM) | Sin probar | Sin probar |
| Windows Server 2025, x86_64 | Emulado, QEMU para Windows | Comprobado en CI | Sin probar |
| Windows 11, ARM64 | Emulado, el QEMU ARM64 de MSYS2 | Comprobado en CI | Sin probar |

«Comprobado en CI» es el flujo de trabajo Hosts del repositorio
(`.github/workflows/hosts.yml`) en las máquinas virtuales de GitHub:
descarga una versión preliminar como lo haría un jugador, arranca la consola,
inserta MUN Collect, lo juega y sale, retira la tarjeta y apaga. Dice que las
herramientas y la imagen funcionan en ese sistema; no mide la velocidad, y no
se ha probado ningún ordenador físico con Linux o Windows. Las consolas
emuladas son varias veces más lentas que las virtualizadas.

Dentro de la consola, los juegos arrancan en la resolución de la consola si
usan SDL u OpenGL, y los de framebuffer, al tamaño que tenía la pantalla al
arrancar ([ejecutar un juego](../runtime.md#display), en inglés).

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
| macOS, Apple Silicon (HVF, `hdiutil` para la semilla del constructor) | Comprobado (builds de unos dos minutos) |
| Linux ARM64 con KVM (`xorriso` para la semilla) | Las herramientas lo admiten; sin probar |
| Linux x86_64, o ARM64 sin KVM | El constructor se emula: horas; sin probar |
| Windows | Sin soporte; WSL 2 sin probar |

## Hardware

Ninguno. MUN™ -1 tendrá una sola configuración de hardware con soporte
oficial, aún no elegida; nada de lo que hay aquí certifica un hardware.
