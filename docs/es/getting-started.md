# Primeros pasos

[English](../getting-started.md) · **Español**

Ejecuta MUN™ OS en tu ordenador como consola virtual: recorre su interfaz,
inserta una Game Card, juega, guarda, retira la tarjeta con seguridad y
continúa más tarde, y después mira cómo un juego viste la consola con MUN
Shape. No hace falta compilar nada: descargas las herramientas y una imagen
ya hecha. Dónde se ha comprobado cada paso está en
[compatibilidad](compatibility.md).

## 1. Qué necesitas

Para jugar: Python 3.9 o posterior, QEMU 8.2 o posterior (su
`qemu-system-aarch64`, `qemu-img` y el firmware UEFI de ARM64) y unos 1,5 GB
de disco libre.

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

Las secciones 2 a 6 no necesitan nada más. La sección 7, MUN Shape, hace una
tarjeta, y las herramientas de tarjetas necesitan además e2fsprogs 1.47 o
posterior: `brew install e2fsprogs` en macOS, el paquete `e2fsprogs` en
Linux; en Windows, WSL 2 (sin probar). Construir la imagen necesita todavía
más ([abajo](#construir-la-imagen-tú-mismo)).

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
`release.json`, que lista cada archivo con su tamaño y su SHA-256. Estas
páginas son de la v0.1.0-dev.3; si todavía no está en la
[página de versiones](https://github.com/Play-MUN/mun-os/releases), usa la
última versión preliminar que haya allí, con las páginas de su propia
etiqueta, o construye la imagen de esta rama
([construir la imagen tú mismo](#construir-la-imagen-tú-mismo)). Pásale a
`./mun get` la dirección de su `release.json`:

```sh
./mun get https://github.com/Play-MUN/mun-os/releases/download/v0.1.0-dev.3/release.json
```

Descarga cada archivo, lo comprueba contra `release.json` e instala la imagen
en `.local/mun/builds/` y las tarjetas en `.local/gamecards/`. Una descarga
interrumpida continúa si vuelves a ejecutar la misma orden. Una Game Card que
ya existe nunca se sustituye (puede tener tus partidas). En Windows:
`py mun get …`.

Si jugaste una versión preliminar anterior, su consola (`play`) sigue con
aquella imagen: una consola conserva la imagen con la que se creó, y una
nueva se crea con la imagen construida más recientemente que tengas, que no
tiene por qué ser esta descarga. Arranca esta por su nombre, en una consola
propia: `./mun dev list` muestra el nombre con que se instaló
(`d<mes><día>-<hora>` de su build, salvo que elijas uno con
`./mun get … --name NAME`).

```sh
./mun play --build NAME --guest NAME
```

Usa `--guest NAME` también en las órdenes de más abajo, y `NAME` donde digan
`play` (`./mun dev vm NAME card-attach collect`,
`./mun dev vm NAME shell-log`, `.local/mun/guests/NAME/`). La consola
anterior se queda como estaba, con sus propios ajustes; tus tarjetas y sus
partidas sirven en las dos.

## 4. Arranca la consola y recórrela

```sh
./mun play
```

La consola se abre en una ventana, con el sonido por la salida del propio
ordenador (ventana, teclado y sonido se han comprobado en macOS por ahora).
La ventana tiene el teclado mientras está delante:

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

La pantalla principal tiene cuatro entradas, cada una con su panel a la
derecha:

- **Game Card**: la ranura. Por ahora está vacía: «Ranura vacía».
- **Mis juegos**: «No hay ningún juego en la consola. Tus juegos viven en
  sus Game Cards.»
- **Configuración**: el idioma (inglés o español), la resolución (la ventana
  la sigue), los sonidos de los menús, MUN Shape y más.
- **Apagar**: apaga la consola limpiamente y cierra la ventana; cerrar tú la
  ventana es como desenchufarla.

La línea de arriba dice qué tarjeta hay dentro, la red (*Sin conexión*: la
consola no la necesita) y la hora.

## 5. Inserta una Game Card y juega

Con la pantalla principal a la vista, desde un segundo terminal en la misma
carpeta:

```sh
./mun dev vm play card-attach collect
```

Eso mete `.local/gamecards/collect.img` en la ranura de la consola, como lo
haría una mano. La consola lee la tarjeta y la comprueba, y *Game Card*
muestra MUN Collect, con su panel diciendo que la tarjeta está lista.
**Intro, Intro** es *Jugar*.

En MUN Collect las flechas mueven el cuadrado, **S** guarda en la tarjeta
(«GUARDADO EN LA GAME CARD») y **Esc** termina el juego. La consola vuelve
con *Sesión terminada*: **Intro** para *Aceptar*.

## 6. Retírala con seguridad y continúa más tarde

En el panel de *Game Card* elige **Retirar con seguridad**: la consola
termina con la tarjeta y la libera («Puedes retirar la Game Card»), y la
ventana la saca de la ranura enseguida: *Game Card* vuelve a estar vacía.
Después, **Apagar**: Arriba desde *Game Card*, Intro, Intro.

Más tarde, la tarjeta entra al arrancar la consola:

```sh
./mun play --card collect
```

Intro, Intro: MUN Collect dice «PARTIDA RECUPERADA» y el cuadrado está donde
lo guardaste. La partida está en la tarjeta (`.local/gamecards/collect.img`),
no en la consola: otra consola, u otra build, continúa desde ella.

Cuando termines, **Esc** acaba el juego, **Intro** es *Aceptar* en *Sesión
terminada* y **Apagar** (Arriba desde *Game Card*, Intro, Intro) cierra la
consola. La sección siguiente arranca una consola propia.

## 7. Mira MUN Shape

Mientras su tarjeta está dentro, un juego puede vestir la consola con su
propia identidad: colores, materiales, el objeto de la tarjeta, un mundo
detrás de los menús, transiciones y sonidos, a partir de un paquete de
recursos de la tarjeta que la consola dibuja con su propio código. Con
e2fsprogs (sección 1), una orden muestra un ejemplo:

```sh
./mun dev shape examples/shape/sea --window
```

Una consola propia (el invitado `shape`, creado la primera vez a partir de
tu imagen más reciente) se abre en una ventana con una tarjeta desechable: tu
MUN Collect vestido con el ejemplo `sea`, insertado cuando ya se ve la
pantalla principal para que veas cómo llega. Juégala, vuelve, retírala;
*Apagar* termina la vista previa. Tus propias tarjetas solo se leen. El otro
ejemplo es `examples/shape/paper`.

*Configuración* › *Imagen y sonido* › *MUN Shape* (Completo, Solo colores,
Desactivado) y *Reducir movimiento* cambian cuánto de ella ves.
[Ver MUN Shape en acción](guides/see-shape-in-action.md) enseña cómo se hace
una tarjeta así y qué mirar;
[vestir la consola con tu juego](guides/shape-your-game.md) hace una tuya.

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
- Algo poco claro o equivocado en estos pasos: dilo en una
  [incidencia](https://github.com/Play-MUN/mun-os/issues/new/choose); eso
  también ayuda.

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
