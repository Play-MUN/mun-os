# Crear una Game Card

[English](../../guides/create-game-card.md) · **Español**

Haz tu propia Game Card con MUN Collect, el juego de ejemplo: dale una
identidad y una portada, compruébala, juégala en la consola, guarda en ella,
retírala y continúa desde ella después de reiniciar la consola. Veinte
minutos, sin compilar nada.

Aquí una Game Card es una imagen de disco ext4; en la consola será una
tarjeta física. Lleva un manifiesto (`mun.toml`), el juego en `content/`, una
portada opcional y un directorio `saves/` donde la consola escribe el
progreso del jugador. Las reglas están en [Game Cards](../../game-cards.md) y
en [partidas](../../saves.md), en inglés.

## Antes de empezar

- Las herramientas y una imagen preliminar, como en
  [primeros pasos](../getting-started.md) (secciones 1 a 3): tendrás
  `.local/gamecards/collect.img`.
- e2fsprogs 1.47 o posterior, que la herramienta de tarjetas usa para
  escribir y leer imágenes de tarjeta sin montarlas: `brew install e2fsprogs`
  en macOS, el paquete `e2fsprogs` en Linux. En Windows: usa WSL 2
  ([compatibilidad](../compatibility.md#hacer-game-cards)).

Esta guía cita los menús de la consola en español; arranca en inglés y se
cambia en *Settings* › *Account and language* › *Language*
([glosario](../glossary.md#las-palabras-de-la-consola)).

## 1. El juego: un programa Linux para ARM64

La consola ejecuta programas Linux para AArch64. MUN Collect es uno: C, sin
bibliotecas, enlazado de forma estática, dibuja en el framebuffer. Sácalo de
la tarjeta que descargaste, en solo lectura, a `.local/`, que Git ignora.
`debugfs` viene con e2fsprogs pero no suele estar en el `PATH`: Homebrew lo
guarda en su propio directorio, y las distribuciones de Linux, en `/sbin`.

```sh
debugfs="$(brew --prefix e2fsprogs)/sbin/debugfs"    # en Linux: debugfs=/sbin/debugfs
mkdir -p .local/mycollect
"$debugfs" -R "dump /content/mun-collect .local/mycollect/mun-collect" .local/gamecards/collect.img
chmod +x .local/mycollect/mun-collect
file .local/mycollect/mun-collect    # ELF 64-bit LSB executable, ARM aarch64, … statically linked
```

`./mun card tools` dice qué e2fsprogs usa la propia herramienta de tarjetas.

O compílalo: una construcción de la imagen lo deja en
`.local/mun/builds/<build>/games/mun-collect/mun-collect`, y cualquier sistema
Debian 13 arm64 con gcc y make lo construye con
`make -C examples/mun-collect` ([el ejemplo](../../../examples/mun-collect/README.es.md)).

## 2. Su identidad y su aspecto

Elige:

| | Opción | Ejemplo |
| --- | --- | --- |
| Título que muestra la consola | `--title` | `"My Collect"` |
| Identificador, el mismo para todas las ediciones del juego; de 3 a 64 letras minúsculas, dígitos, `.`, `-`, `_` | `--id` | `org.example.mycollect` |
| Versión del contenido | `--version` | `1.0.0` |
| Portada: un PNG de 1 MiB y 1024×1024 como máximo | `--cover` | `cover.png` |
| Colores que toma la consola mientras la tarjeta está dentro: el foco y la luz del centro | `--accent`, `--background` | `"#2E7EC5"`, `"#BFD9F2"` |

Las partidas pertenecen al identificador: mantenlo cuando hagas una versión
nueva del mismo juego y cámbialo para un juego distinto. La portada y los
colores son opcionales; sin portada, la herramienta dibuja una.

## 3. Hacer la tarjeta

```sh
./mun card create mycollect --variant game --game .local/mycollect/mun-collect \
    --title "My Collect" --id org.example.mycollect --version 1.0.0 \
    --accent "#2E7EC5" --background "#BFD9F2"
```

`--variant game` es un juego para el perfil de framebuffer, como MUN Collect;
`game-gl` es para juegos hechos con SDL2, OpenGL y OpenAL, con `--content`
para sus datos ([traer un juego a MUN™](port-a-game.md)). La tarjeta se
escribe en `.local/gamecards/mycollect.img` (64 MiB; `--size` lo cambia), con
unos pocos archivos de ejemplo de la herramienta junto al juego en
`content/`. Un identificador o una versión que la consola rechazaría se
rechaza aquí, y una tarjeta existente nunca se sobrescribe sin `--force`.

## 4. Comprobarla

```sh
./mun card inspect mycollect
```

```text
.local/gamecards/mycollect.img
  VÁLIDA  My Collect (org.example.mycollect) v1.0.0 · game · aarch64/linux-arm64-v0 · portada: cover.png · declara ejecutable content/mun-collect
  nombres: MUN (mun.toml, partidas mun-save/1)
  sha256 … · sin cambios tras inspección: sí
```

`inspect` ejecuta sobre la imagen el mismo validador que la consola, sin
montarla ni cambiarla (las herramientas de tarjetas hablan español, como los
servicios de la consola). Una tarjeta que rechaza, la consola también la
rechaza, por el mismo motivo. `./mun card hash mycollect --files` lista cada
archivo de la tarjeta con su SHA-256.

## 5. Jugar y guardar

```sh
./mun play --card mycollect
```

La consola arranca en una ventana y la tarjeta entra: la entrada *Game Card*
dice «My Collect», con tu color de acento. **Intro, Intro** juega. Mueve el
cuadrado con las flechas, recoge un disco o dos y pulsa **S**: «GUARDADO EN
LA GAME CARD». La consola ha escrito tu progreso en `saves/` de la tarjeta,
de forma atómica y conservando la copia anterior. **Esc** termina el juego;
**Intro** para *Aceptar* en *Sesión terminada*.

## 6. Retirar, reiniciar, continuar

En el panel de la tarjeta elige **Retirar con seguridad**: la consola termina
con la tarjeta, la desmonta y la tarjeta sale de la ranura («Puedes retirar
la Game Card»). Después, **Apagar** (Arriba desde *Game Card*, Intro, Intro):
la consola se apaga y la ventana se cierra.

Arráncala otra vez, con la tarjeta:

```sh
./mun play --card mycollect
```

Intro, Intro: «PARTIDA RECUPERADA», con el cuadrado donde lo guardaste. El
progreso estaba en la tarjeta, no en la consola; otra consola, o una imagen
construida otro día, continúa desde ella del mismo modo.

`./mun card hash mycollect --files --ignore saves` da las mismas huellas que
antes de jugar: el juego y su contenido nunca cambian, solo `saves/`.

## Empaquetar no es portar

Una Game Card lleva un programa que la consola puede ejecutar: un ejecutable
Linux para AArch64, para uno de los perfiles de ejecución de la consola, con
las bibliotecas que ese perfil aporta. Poner en una tarjeta un programa de
Windows o de macOS, o uno de Linux para x86_64, no lo convierte en un juego
de MUN: la tarjeta es válida y el juego no arranca. Traer un juego a MUN es
construirlo para Linux ARM64 contra las bibliotecas de la consola y hacer
que guarde donde la consola guarda las partidas:
[traer un juego a MUN](port-a-game.md).

## Para ir más allá

- La identidad de tu juego en la consola mientras su tarjeta está dentro
  (colores, un mundo, una transición, sonidos):
  [vestir la consola con tu juego](shape-your-game.md).
- Tarjetas rotas a propósito, para ver cómo las rechaza la consola:
  `./mun card variants` y después `./mun card create broken --variant bad-arch`.
- Dos consolas, una tarjeta: una tarjeta solo está en una consola en marcha a
  la vez; las órdenes del laboratorio insertan y retiran tarjetas desde un
  script ([laboratorio](../../../vm/README.md#game-cards), en inglés).
- Los contratos: [Game Cards](../../game-cards.md),
  [partidas](../../saves.md) y [ejecutar un juego](../../runtime.md), en
  inglés.
