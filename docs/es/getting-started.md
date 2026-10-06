# Primeros pasos

[English](../getting-started.md) · **Español**

Ejecuta MUN™ OS en tu ordenador como consola virtual, juega la Game Card que
viene con él, guarda y continúa más tarde. No hace falta compilar nada:
descargas las herramientas y una imagen ya hecha. Dónde se ha comprobado cada
paso está en [compatibilidad](compatibility.md).

## 1. Qué necesitas

Python 3.9 o posterior, QEMU 8.2 o posterior (su `qemu-system-aarch64`,
`qemu-img` y el firmware UEFI de ARM64) y unos 1,5 GB de disco libre.

- **macOS**: `brew install qemu` ([Homebrew](https://brew.sh)). El `python3`
  del sistema sirve.
- **Debian o Ubuntu**: `sudo apt install qemu-system-arm qemu-utils
  qemu-efi-aarch64 python3`, y `qemu-system-gui` para la ventana. Otras
  distribuciones empaquetan los mismos programas; el firmware suele llamarse
  AAVMF o edk2-aarch64.
- **Windows en un ordenador x86_64**: [QEMU para Windows](https://www.qemu.org/download/#windows)
  (su instalador pone el firmware junto a `qemu-system-aarch64.exe`; las
  herramientas también lo buscan en `C:\Program Files\qemu`) y Python de
  [python.org](https://www.python.org/downloads/windows/). Ejecuta las
  herramientas como `py mun …`.
- **Windows en un ordenador ARM64**: QEMU para Windows está hecho para x86_64
  y ahí no puede ejecutar la consola. Usa la build ARM64 de
  [MSYS2](https://www.msys2.org): en su shell CLANGARM64, `pacman -S
  mingw-w64-clang-aarch64-qemu mingw-w64-clang-aarch64-qemu-image-util` (las
  herramientas también lo buscan en `C:\msys64\clangarm64\bin`), y Python de
  python.org.

## 2. Las herramientas

Las herramientas son este repositorio. O lo clonas:

```sh
git clone https://github.com/Play-MUN/mun-os.git
cd mun-os
```

o descargas el archivo *Source code* de una
[versión preliminar](https://github.com/Play-MUN/mun-os/releases) y lo
descomprimes. Cuando puedas, usa las herramientas de la misma versión que la
imagen.

## 3. La imagen

Una versión preliminar trae la imagen, su registro (`BUILD-INFO.json`), dos
Game Cards (MUN Collect y la MUN Test Card), los textos de las licencias y
`release.json`, que lista cada archivo con su tamaño y su SHA-256. Pásale a
`./mun get` la dirección de ese `release.json`:

```sh
./mun get https://github.com/Play-MUN/mun-os/releases/download/v0.1.0-dev.2/release.json
```

Descarga cada archivo, lo comprueba contra `release.json` e instala la imagen
en `.local/mun/builds/` y las tarjetas en `.local/gamecards/`. Una descarga
interrumpida continúa si vuelves a ejecutar la misma orden. Una Game Card que
ya existe nunca se sustituye (puede tener tus partidas). En Windows:
`py mun get …`.

## 4. Jugar

```sh
./mun play --card collect
```

La consola se abre en una ventana, con el sonido por la salida del propio
ordenador (ventana y sonido se han comprobado en macOS por ahora), y MUN
Collect entra en cuanto arranca. La ventana tiene el teclado mientras está
delante:

| Tecla | En MUN Shell |
| --- | --- |
| Arriba, Abajo | Moverse por el menú |
| Intro (o A) | Elegir, entrar |
| Esc (o B) | Volver |
| Izquierda, Derecha | Cambiar un ajuste |

La consola arranca en inglés. Para verla en español, elige *Español* en
*Settings* › *Account and language* › *Language*; desde entonces los menús
dicen *Configuración* › *Cuenta e idioma* › *Idioma*, y esta guía usa esas
etiquetas ([glosario](glossary.md#las-palabras-de-la-consola)).

La pantalla principal se abre en *Game Card*: **Intro, Intro** es *Jugar*. En
MUN Collect las flechas mueven el cuadrado, **S** guarda en la tarjeta
(«GUARDADO EN LA GAME CARD») y **Esc** termina el juego; la consola vuelve
con *Sesión terminada*: **Intro** para *Aceptar*.

*Configuración* tiene el idioma (inglés o español), la resolución (la ventana
la sigue), los sonidos de los menús y más. **Apagar** (Arriba desde *Game
Card*) apaga la consola limpiamente y cierra la ventana; cerrar tú la ventana
es como desenchufarla.

## 5. Continuar más tarde

```sh
./mun play --card collect
```

Intro, Intro: MUN Collect dice «PARTIDA RECUPERADA» y el cuadrado está donde
lo guardaste. La partida está en la tarjeta (`.local/gamecards/collect.img`),
no en la consola: otra consola, u otra build, continúa desde ella.

Para retirar una tarjeta con la consola en marcha, elige **Retirar con
seguridad** en su panel: la consola termina con ella y la tarjeta sale de la
ranura.

## Dónde está cada cosa

| Ruta | Qué |
| --- | --- |
| `.local/mun/builds/<name>/` | Una imagen instalada, su `BUILD-INFO.json`, sus licencias |
| `.local/mun/guests/<name>/` | El disco, los registros y las capturas de una consola virtual (`./mun play` usa `play`) |
| `.local/gamecards/*.img` | Las Game Cards, con sus partidas |

`./mun dev list` lista las builds y las consolas. `./mun play --build NAME
--guest NAME` arranca otra consola sobre otra imagen; una consola queda
ligada a la imagen con la que se creó.

## Si algo va mal

- *No ARM64 UEFI firmware found*: instala el paquete del firmware de arriba,
  o indica el archivo con `MUN_VM_FIRMWARE`.
- *QEMU … has no virtio-sound device*: QEMU es anterior a la 8.2; actualízalo
  o arranca con `--audio off`.
- En Windows ARM64, *QEMU's x86_64 build, which cannot run the console*: usa
  la build ARM64 de MSYS2, de arriba.
- Los registros de la consola: `./mun dev vm play shell-log`, `launchd-log`,
  `cardd-log`; su consola serie: `.local/mun/guests/play/console.log`.

## Construir la imagen tú mismo

Construir es aparte de jugar y necesita más: un Mac con Apple Silicon (las
herramientas de línea de órdenes de Xcode, Python y `brew install qemu
e2fsprogs`), o un ordenador ARM64 con Linux y KVM (Git, Make, OpenSSH, QEMU,
e2fsprogs, `xorriso`; aún sin probar). Windows no construye (usa WSL 2). Una
build se ejecuta en una VM constructora desechable, descarga sus entradas con
versión fijada de una instantánea fechada de Debian y ocupa unos 10 GB de
disco:

```sh
make check                        # las comprobaciones del anfitrión
./mun dev build --name one        # → .local/mun/builds/one/, con los juegos de ejemplo junto a la imagen
./mun card create mycollect --variant game --game .local/mun/builds/one/games/mun-collect/mun-collect
./mun play --build one --guest mine --card mycollect
```

Una build deja sus juegos junto a la imagen, no en tarjetas: la tercera línea
hace una tarjeta de MUN Collect con la copia de esta build
([crear una Game Card](guides/create-game-card.md) lo explica).

Cómo se compone una imagen y qué registra una build:
[construcción de la imagen](../../os/README.md) (en inglés). Consolas en
segundo plano, teclas y capturas desde un script, inserción de tarjetas
virtuales: [laboratorio](../../vm/README.md) (en inglés).
