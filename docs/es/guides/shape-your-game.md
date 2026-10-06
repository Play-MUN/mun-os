# Vestir la consola con tu juego (MUN Shape)

[English](../../guides/shape-your-game.md) · **Español**

Mientras tu Game Card está en la consola, MUN puede tomar la identidad de tu
juego: sus colores en el menú, cristal o papel bajo las palabras, el objeto
de la tarjeta con tu arte, un mundo que se mueve detrás de la pantalla
principal, una transición que la trae y se la lleva, y los sonidos de tu
juego en los menús. Esto es MUN Shape v1. Es opcional y funciona sin
conexión, es una carpeta de tu tarjeta, y la consola lo dibuja con su propio
código: no escribes software para ello y nada de tu juego cambia.

Esta guía va de una carpeta vacía a una tarjeta que juegas y retiras, con los
paquetes de ejemplo de [`examples/shape`](../../../examples/shape/README.es.md):
crea un paquete, añade tus recursos, compruébalo, previsualízalo en la
consola, haz la tarjeta, insértala, juega, vuelve, retírala. Las reglas, con
todas sus cifras, son el contrato: [MUN Shape](../../shape.md), en inglés.

Esta guía cita los menús de la consola en español; arranca en inglés y se
cambia en *Settings* › *Account and language* › *Language*
([glosario](../glossary.md#las-palabras-de-la-consola)).

## Antes de empezar

- Las herramientas, como en [primeros pasos](../getting-started.md)
  (secciones 1 y 2), y una imagen que muestre MUN Shape, construida desde tu
  copia del repositorio como en
  [construir la imagen tú mismo](../getting-started.md#construir-la-imagen-tú-mismo)
  (`./mun dev build`): la v0.1.0-dev.2 publicada, la que descarga
  `./mun get`, no muestra MUN Shape (una tarjeta con paquete es válida y se
  juega en ella, con el aspecto de MUN). `./mun dev list` dice qué imágenes
  lo muestran: `shape mun-shape/1`.
- e2fsprogs 1.47 o posterior para la herramienta de tarjetas
  ([crear una Game Card](create-game-card.md#antes-de-empezar)).
- Un juego en una tarjeta, o MUN Collect como en
  [crear una Game Card](create-game-card.md) (secciones 1 a 3). La vista
  previa de abajo no necesita ninguno: viste MUN Collect por ti.

## 1. Crea un paquete

```sh
./mun card shape init .local/mypkg --example sea      # una copia de un ejemplo para cambiarla
./mun card shape init .local/mypkg --cover cover.png  # o una plantilla, con la paleta leída de tu portada
```

`--example` copia uno de los dos ejemplos (`sea`: cristal azul profundo, un
mundo bajo el agua, una marea; `paper`: papel crema, montañas de tinta, un
barrido). La plantilla a partir de una portada declara una paleta (la que la
consola leería de esa portada), el objeto de la tarjeta, las superficies y
una transición, que no necesitan archivos; su README dice cómo añadir un
mundo y sonidos. `init` nunca escribe sobre archivos que ya tienes, salvo que
lo pidas con `--force`. El paquete vive en `.local/`, que Git ignora, como el
resto de tu trabajo en estas guías: una construcción rechaza una copia del
repositorio con archivos que Git no conoce.

## 2. Qué lleva

```text
.local/mypkg/
  shape.json            el paquete: JSON, formato "mun-shape/1"
  card/window.png       la pantalla del objeto de la tarjeta
  world/backdrop.png    el mundo detrás de la pantalla principal: fondo, capas, sprites, luz
  world/…
  sfx/move.wav          los sonidos de los menús, y el aviso de inserción
  sfx/enter.wav
  sfx/back.wav
  sfx/insert.wav
```

`shape.json` tiene seis bloques, todos opcionales:

| Bloque | Qué cambia | Archivos |
| --- | --- | --- |
| `palette` | seis colores: `light`, `mid`, `deep`, `plate`, `text`, `accent` (el foco) | ninguno |
| `card` | el objeto de la tarjeta: la imagen de su ventana (o tu portada), el contorno `card` u `organic`, su luz | `window` |
| `world` | detrás de la pantalla principal: un fondo (imagen o degradado), hasta 3 capas, 2 emisores de sprites (128 sprites) y 2 texturas de luz, a 10 o 20 fotogramas por segundo | imágenes |
| `surfaces` | el material de las entradas del menú y del panel de tu juego: `solid`, `glass` o `paper` | ninguno |
| `transition` | cómo entra y se va la identidad: `tide`, `sweep` o `fade`, de 0,8 a 4 s | ninguno |
| `sounds` | `move`, `enter` y `back` en los menús, y el aviso `insert`, opcional | WAV |

Solo se leen los archivos que nombra `shape.json`; los demás de la carpeta
(un README, tu arte original) se quedan en tu ordenador. Los campos y sus
valores por defecto están en [el contrato](../../shape.md#structure); cómo se
dibuja cada elemento de un mundo (deriva, balanceo, paralaje, las
trayectorias de los emisores, los movimientos de las texturas de luz) está en
[cómo dibuja la consola un mundo](../../shape.md#how-the-console-draws-a-world),
los dos en inglés, para que lo que diseñes sea lo que muestra la consola.

## 3. Añade tus recursos

Sustituye los archivos del ejemplo por los tuyos, con los mismos nombres o
con nombres nuevos que indiques en `shape.json`.

| Tipo | Formato | Límites |
| --- | --- | --- |
| Imágenes | PNG (cualquier profundidad de bits y tipo de color que admita PNG) | 4 MiB cada una; el lado mayor, 2048 para un fondo, una capa o una textura de luz, 1024 para la ventana de la tarjeta, 256 para un sprite |
| Sonidos | WAV, PCM, 48 kHz, 16 bits, estéreo, solo con `fmt ` y `data` | 1 MiB cada uno; `move`, `enter` y `back`, 1 s como máximo, e `insert`, 3 s como máximo; picos de −1 dBFS o menos |
| El paquete | los archivos que nombra `shape.json` | 32 MiB en total |

- Un fondo cubre el lienzo, centrado (1920×1080 es el lienzo de diseño; una
  imagen 16:9 se ve entera).
- Una capa se escala a la altura de la pantalla y se repite a lo ancho: haz
  que su borde derecho encaje con el izquierdo. Sus filas transparentes no
  cuestan nada.
- Los sprites miran a la derecha; se reflejan cuando viajan hacia la
  izquierda.
- Una textura de luz se estira sobre la pantalla y se le suma o se combina
  con ella en modo trama (screen): que sea suave.
- Para hacer un WAV que la consola acepte:
  `ffmpeg -i in.wav -ar 48000 -ac 2 -c:a pcm_s16le -fflags +bitexact -map_metadata -1 out.wav`.

Usa solo arte y sonidos que tengas derecho a poner en la tarjeta.

## 4. Compruébalo

```sh
./mun card shape check .local/mypkg --report
```

El validador lee la carpeta exactamente como una consola lee una tarjeta. Su
primera línea es el veredicto:

- `LISTO`: la consola usaría todo lo declarado; código de salida 0.
- `PARCIAL`: usaría una parte, y el resto vuelve a ser de MUN; código de
  salida 2.
- `SIN USO`: no usaría el paquete; la tarjeta sigue siendo válida.

Después, una línea por bloque, `declarado` o `DESCARTADO`, con el motivo y el
campo (las herramientas de tarjetas hablan español, como los servicios de la
consola). Después, los colores de las superficies, la opacidad con que se
dibuja cada placa y el plan de transición de cada superficie. `--report`
añade las relaciones de contraste y, a 1080p y 1440p, el nivel de detalle con
que una consola dibujaría tu mundo, con lo que ocupa, calculado como lo
calcula la consola. `--json` lo da todo para tus propios scripts. Corrige lo
que señale y vuelve a comprobar; todavía no hace falta ninguna consola.

## 5. Previsualízalo en la consola

```sh
./mun dev shape .local/mypkg --watch --window
```

Una consola del laboratorio arranca en una ventana (el invitado `shape`,
creado la primera vez a partir de tu imagen más reciente) con una tarjeta
desechable: MUN Collect vestido con tu paquete. Lo ves como lo verá un
jugador: la transición, el mundo, los sonidos de los menús, el panel. Edita
un archivo y guárdalo: cuando la carpeta lleva un segundo quieta, la vista
previa la comprueba, espera a que no se esté jugando y a que vuelva el menú
de la consola, saca la tarjeta con la retirada segura de la consola e inserta
una nueva con tu cambio. Cada cambio es una inserción nueva, así que ves tu
transición de llegada y oyes tu aviso cada vez.

- La consola tiene que mostrar MUN Shape (`./mun dev list` muestra
  `shape mun-shape/1` para su build y su invitado). Un invitado creado antes a
  partir de una imagen que no lo muestra se rechaza, y la vista previa dice
  cómo conseguir uno que sí: un invitado nuevo de tu imagen más reciente
  (`--guest NAME`), o `./mun dev vm shape destroy --yes` y empezar de nuevo.
- Un cambio que la consola no usaría en absoluto se indica en el terminal, y
  la tarjeta de la consola se queda como estaba.
- `--base mygame` viste una copia de una de tus tarjetas en lugar de MUN
  Collect (la tarjeta en sí solo se lee).
- La vista previa usa sus propias tarjetas (`pv0-shape`, `pv1-shape`) y nada
  más: no arranca en una consola que tenga otra tarjeta dentro, y nunca saca
  ni borra una tarjeta tuya, aunque la hayas llamado `pv0-shape` (se detiene
  y nombra el archivo). Se ejecuta una sola vista previa por consola.
- Sin `--window`, la consola funciona sin ventana (mírala con
  `./mun dev vm shape screenshot`), y Ctrl-C termina la vista previa: la
  tarjeta sale de forma segura y la consola sigue encendida
  (`./mun dev vm shape stop` la apaga). Con ventana, *Apagar* en la consola
  la termina. `./mun dev vm shape destroy --yes` elimina la consola.

## 6. Haz la tarjeta

Añade `--shape` a la orden que hace tu tarjeta:

```sh
./mun card create mygame --variant game --game .local/mycollect/mun-collect \
    --title "My Game" --id org.example.mygame --version 1.0.0 --shape .local/mypkg
```

`--shape` copia `shape.json` y los archivos que nombra a `content/mun-shape/`
de la tarjeta, y rechaza un paquete que la consola no usaría entero;
`--shape-partial` hace la tarjeta de todos modos (para probar cómo se repliega
la consola, por ejemplo). Una tarjeta `game-gl` hecha con `--content DIR`
puede llevar en su lugar el paquete como `DIR/mun-shape/`; en ese caso no
indiques también `--shape`.

Después comprueba la propia tarjeta, como la encontrará la consola:

```sh
./mun card inspect mygame          # la tarjeta y un resumen: MUN Shape: mun-shape/1 · LISTO …
./mun card shape check mygame      # el paquete de la tarjeta, bloque a bloque
```

## 7. Inserta, juega, vuelve, retira

```sh
./mun play                                  # una consola sin tarjeta, en una ventana
./mun dev vm play card-attach mygame        # cuando se vea la pantalla principal, desde otro terminal
```

`./mun play --card mygame` mete la tarjeta al arrancar la consola: la consola
la encuentra ahí y se viste enseguida, sin la llegada ni el aviso. Para
verlos, mete la tarjeta cuando ya se vea la pantalla principal, como arriba
([ver MUN Shape en acción](see-shape-in-action.md) lo recorre).

- **Insertar.** La tarjeta se lee, el objeto de la tarjeta toma tu ventana,
  después tu transición trae la identidad desde el objeto hacia fuera y tu
  aviso suena una vez. Navega: tus sonidos en el menú, tu panel, las palabras
  de MUN.
- **Jugar.** *Jugar* arranca el juego enseguida: el color profundo del juego
  crece desde el objeto de la tarjeta mientras la consola le cede la
  pantalla. Ninguna animación lo hace esperar.
- **Volver.** Cuando el juego termina, la consola vuelve con tu identidad ya
  puesta: un fundido breve, sin transición desde el objeto ni aviso, incluso
  bajo el diálogo *Sesión terminada*.
- **Retirar.** *Retirar con seguridad* mantiene tu identidad hasta que la
  consola ha terminado con la tarjeta y la ha liberado; solo entonces se va la
  identidad, con tu transición de salida, y aparece «Puedes retirar la Game
  Card». Si la consola no puede liberar la tarjeta, la identidad se queda.
- **Sacarla** sin retirarla con seguridad, incluso a mitad de una transición:
  MUN vuelve en medio segundo, por el mismo camino por el que entró tu
  identidad. En el laboratorio, `./mun dev vm play card-detach mygame --abrupt`
  saca la tarjeta; la ranura virtual informa de la retirada unos segundos
  después.

## Lo que vistes y lo que sigue siendo de MUN

Se viste mientras tu tarjeta es la activa:

- el mundo de la pantalla principal detrás del menú, y el objeto de la
  tarjeta;
- las entradas del menú principal y el panel de tu juego: placas de tu
  material, tu color de texto y tu foco;
- las bandas bajo la línea de estado, la ruta y las indicaciones;
- los sonidos de los menús y tu aviso de inserción.

Siempre de MUN: Configuración (su menú, sus paneles, su foco y sus sonidos),
todos los diálogos y sus sonidos, el arranque y el apagado, las palabras del
traspaso al juego, la disposición, los tamaños y el orden, todas las palabras
y su idioma, y los colores de las luces indicadoras (van sobre zócalos
propios de MUN encima de tus bandas). Configuración y los diálogos aparecen
sobre tu mundo bajo un velo fijo de MUN, para que se lean igual con cualquier
juego.

## Cuando falta o falla un recurso

Un paquete nunca invalida una tarjeta, y la tarjeta se puede jugar mientras
la consola todavía lo prepara. *Retirar con seguridad* cancela esa
preparación y puede esperar a que cierre los archivos de la tarjeta: como
mucho 3 s; después la consola dice que la tarjeta sigue en uso, que no la
retires todavía, y la retirada queda pendiente hasta que puede terminar
([MUN Shape](../../shape.md#how-the-console-uses-a-package), en inglés). Un
bloque se usa entero o se descarta entero:

| Qué está mal | Qué hace la consola |
| --- | --- |
| `shape.json` falta, no es JSON, supera 64 KiB o es de otra versión mayor | No usa el paquete: MUN, con los colores que presta tu tarjeta o que lee de su portada |
| Un campo fuera de rango, un valor desconocido, un color incorrecto, demasiados elementos | Descarta ese bloque y usa el resto |
| Un archivo nombrado que falta, un enlace, uno demasiado grande, un PNG o un WAV mal formado | Descarta el bloque que lo nombra |
| Los colores de la paleta no mantienen el contraste | Tus superficies usan juntos los colores de MUN; tu mundo se sigue dibujando |
| Una imagen que la consola no puede decodificar | El mundo se va (la paleta sobre el mundo de MUN), o el objeto de la tarjeta es entero el de MUN |
| Un mundo demasiado grande para la pantalla | Se dibuja con un nivel de detalle menor (abajo) |
| Un campo desconocido, o una versión menor más nueva | Se ignora, con una nota; se usa el resto |

El validador nombra cada caso con un código; `./mun card shape variants`
escribe un paquete defectuoso pequeño por caso, para verlos en la consola.

## Contraste, movimiento reducido y sonidos

- **El contraste se demuestra, no se espera.** El texto mantiene 4,5:1 y el
  foco 3:1 sobre cualquier mundo, de negro a blanco, en cada fotograma de una
  transición. Para ello la consola calcula la opacidad de cada placa; un
  cristal translúcido se vuelve tan opaco como necesiten tus colores. Si tus
  colores no aguantan en todas las superficies, se usa el juego de colores de
  MUN en todas. `check --report` muestra las relaciones y las placas antes de
  que hagas una tarjeta.
- **Reducir movimiento** (*Configuración* › *Imagen y sonido*): todas las
  transiciones son fundidos y tu mundo se queda quieto. Diséñalo para que se
  lea bien quieto.
- **MUN Shape: Completo, Solo colores, Desactivado.** Solo colores mantiene
  tu paleta y tus placas, sin mundo, imágenes ni sonidos; Desactivado es MUN
  solo.
- **Sonidos.** Con *Sonidos del sistema* desactivados se callan todos,
  también los tuyos; con *Sonidos del juego en los menús* desactivados, tus
  menús tienen los sonidos de MUN y el aviso no suena. El aviso suena una vez
  por inserción, para una tarjeta insertada con la consola encendida (en
  Configuración o bajo un diálogo, espera a la pantalla principal), nunca para
  una tarjeta encontrada al arrancar ni después de una partida.

## Compatibilidad y degradación

- **Versiones.** `"format": "mun-shape/1"`. Una consola lee cualquier `1.x` e
  ignora, con una nota, lo que no conoce; un paquete de otra versión mayor no
  se usa. Las tarjetas de cualquiera de las dos generaciones de nombres, y las
  convertidas con `./mun card convert`, llevan el paquete igual.
- **Sin paquete.** Los colores de `[presentation]` de una tarjeta, o la
  paleta leída de su portada, siguen tiñendo MUN mientras está dentro.
- **El presupuesto de la pantalla.** Antes de decodificar nada, la consola
  estima lo que ocupa tu mundo en su pico y lo dibuja al nivel más rico que
  cabe: completo; sin texturas de luz; el fondo y la capa más cercana a 10
  fotogramas por segundo; quieto; ninguno. `check --report` te dice qué nivel
  dibujaría cada pantalla. Mientras el jugador navega, si el menú se retrasa,
  el mundo baja un nivel durante el resto de la inserción. La navegación va
  siempre primero.
- **Lo que se ha medido.** En la consola virtual del laboratorio (sin GPU), un
  mundo como el del ejemplo `sea` mantiene el nivel completo a 1080p; a 1440p
  la consola está en el límite y puede bajar una vez, al nivel sin texturas de
  luz, que son la mayor parte del coste de un mundo. Un mundo como el de
  `paper`, a 10 fotogramas por segundo y sin texturas de luz, cuesta más o
  menos lo que MUN solo ([medido](../../shape.md#measured), en inglés).
  Todavía no se ha elegido el hardware de la consola: da a las texturas de luz
  solo lo que valen.

## Para ir más allá

- [Ver MUN Shape en acción](see-shape-in-action.md): qué lleva una tarjeta
  vestida, sea cual sea el juego, y un recorrido por ella en una consola.
- [MUN Shape](../../shape.md), el contrato, en inglés: cada campo, regla y
  código.
- [Los paquetes de ejemplo](../../../examples/shape/README.es.md) y cómo se
  hace su arte.
- [Crear una Game Card](create-game-card.md) y
  [traer un juego a MUN](port-a-game.md).
