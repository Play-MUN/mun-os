# Reef — ejemplo de MUN Shape

[English](README.md) · **Español**

**Borrador, experimental.** El arrecife usa los bloques de movimiento de
MUN Shape (`mun-shape/1.1`, [docs/shape-motion.md](../../../docs/shape-motion.md),
en inglés), que están en desarrollo; sus nombres, límites y presupuestos
aún pueden cambiar. Las consolas que solo leen `mun-shape/1`, entre ellas
las versiones preliminares hasta la v0.1.0-dev.4, muestran su aspecto v1:
el póster quieto, la paleta, los sonidos y la ventana; solo una consola
construida desde este código reproduce el movimiento
([probarlo](../../../docs/shape-motion.md#trying-it), en inglés).

Reef — ejemplo de MUN Shape, el arrecife para abreviar, es una escena
marina construida en código convertida en el Shape de una Game Card: una
escena 3D horneada en imágenes, y esas imágenes puestas en
movimiento por el reproductor de la consola. Es el ejemplo oficial del
movimiento de MUN Shape, pensado como primera plantilla: cópiala, cambia lo
que necesites y mira cómo está hecha cada cosa.

## Tres partes

| Parte | Dónde | Qué es | Quién la escribe |
| --- | --- | --- | --- |
| El reproductor | [`services/mun-shell`](../../../services/mun-shell/README.md) (`MotionScene`, en inglés) | El reproductor del movimiento de Shape de MUN Shell: el mismo para cualquier paquete, sin nada propio del arrecife | MUN OS |
| Las fuentes y las herramientas | esta carpeta | La escena (`sea.js`), el horneado (`bake.*`) y el script que hace el paquete (`make_package.py`) | tú, cuando cambias el arrecife |
| El paquete | [`../reef`](../reef) | `shape.json` con sus imágenes y sonidos, tal como los lee una consola; el comprobador lo da por listo (lo prueba `make check`) | solo `make_package.py`: nunca a mano |

Un cambio del arrecife se hace aquí y se vuelve a hornear; `../reef` es lo
que sale, y se confirma en el mismo commit que el cambio que lo produjo.
Para usarlo en otro sitio (una web, un kit de desarrollo), el arrecife se
toma entero de un commit, con sus fuentes: [llevarlo a otro
sitio](#llevarlo-a-otro-sitio).

| Lo que ves | Cómo lo mueve la consola |
| --- | --- |
| El fondo, las rocas, los corales y el agua | Cuatro planos, uno por franja de profundidad, más anchos que la pantalla; la vista deriva despacio y cada uno se mueve según su profundidad (`scene.sway`, `scene.depth`) |
| La luz moviéndose en la arena | Por franja de profundidad, un bucle de 16 fotogramas que se funden entre sí, respirando (animaciones `blend`, `vary opacity`) |
| Las algas meciéndose | 22 tallos, cada uno en su sitio y su profundidad, doblándose desde el pie (`copies.places`, `copies.clips`, `bend`) |
| Los rayos desde la superficie | Siete rayos que oscilan un poco y titilan (`vary rotate`, `vary opacity`) |
| Tres bancos de peces | Formaciones que giran en profundidad, cada pez visto según su rumbo (16 rumbos) con la cola batiendo, en tres tonos, apartándose del tiburón (`views`, `face: views`, `copies`, `avoid`, recorridos con `pace: even`) |
| Un tiburón al fondo | Dando vueltas despacio, según su rumbo, con el cuerpo ondulando |
| Dos cangrejos | De lado, un trecho y vuelta, balanceándose (`vary` con `phase`) |
| Seis medusas | Latiendo, subiendo, desvaneciéndose arriba y volviendo a aparecer abajo (`saw` y `fade` del mismo periodo) |
| Burbujas y nieve marina | Partículas que el disco atrae y se traga (`pull`) |
| Jugar en foco | Crece la luz del disco y el banco ámbar se arremolina por debajo del objeto de la tarjeta; al jugar, el banco se zambulle en el disco; al volver del juego sale de él y vuelve a dar vueltas (`react`). Las tres cosas siguen al objeto de la tarjeta dondequiera que lo ponga Home: la luz y la zambullida van al ancla `disc`, y los puntos del remolino se miden desde ella (`origin`); si el lanzamiento se rechaza, el banco vuelve |

## Empezar desde ella

```sh
./mun card shape init .local/my-shape --example reef     # una copia del paquete
./mun card shape check .local/my-shape --report           # lo que lee la consola y lo que cuesta
```

El paquete (`../reef`) son imágenes y `shape.json`; esta carpeta es lo que
las hace. Para cambiar la escena en sí, copia también esta carpeta.

## Cómo está hecha

| Archivo | Qué hace |
| --- | --- |
| `sea.js` | La escena 3D en three.js: agua, fondo, rocas, corales, algas, los bancos, el tiburón, los cangrejos, las medusas, los rayos, la portada del juego. `buildSea()` la crea y `seaAt(t)` coloca cada cosa en el instante `t` |
| `bake.js`, `bake.html` | Renderiza la escena en una página del navegador con el aspecto de píxel de la consola (640 × 360, color de 15 bits, tramado ordenado) y mide dónde va cada cosa en el lienzo de 1920 × 1080 |
| `bake.mjs` | Lanza el horneado en un Chrome o Chromium sin ventana y escribe `render/` (imágenes PNG y `layout.json`) |
| `make_package.py` | Convierte `render/` en el paquete: `shape.json` con el movimiento, las imágenes y los sonidos del ejemplo `sea`. Las decisiones de comportamiento (ritmo, reacciones, partículas) son constantes al principio del archivo |

Necesitas Node 22 o posterior, un Chrome o Chromium (o `CHROME` apuntando a
uno) y Python 3. `npm ci` instala three.js (0.187.0) aquí y nada más; no
forma parte del paquete ni de la consola.

```sh
cd examples/shape/reef-studio
npm ci
node bake.mjs                  # render/: unos 10 segundos
python3 make_package.py        # ../reef
../../../mun card shape check ../reef --report
```

El paquete de aquí se horneó con Node 24.14.0 y Chrome for Testing
145.0.7632.6 (descargado por una herramienta de pruebas de navegador), sin
ventana, en un Mac con Apple Silicon; horneado de nuevo allí, sale
idéntico byte a byte.
Otro navegador, otra GPU u otro zlib pueden redondear algunos píxeles o
comprimirlos de otra manera: compara lo que muestran las imágenes, no sus
bytes.

## Cambiarla

- **El color o el tamaño de un banco:** `tones` y `SCHOOLS` en `sea.js`, y
  los tonos en `bake.js`; después hornea y empaqueta.
- **Por dónde nada un banco:** `where()` en `sea.js` y el recorrido que
  `bake.js` muestrea de ella (32 puntos en una vuelta, alcanzados a tiempos
  iguales, así que el banco va más despacio cuando se mueve en profundidad,
  como en 3D).
- **Qué responde a Jugar y cómo:** `ANSWERS`, `GATHER` y `react` en
  `make_package.py`; no hace falta hornear.
- **Una criatura propia:** añádela en `sea.js` después de las demás (los
  números de la semilla se sacan en orden: lo que va antes conserva su
  aspecto), renderízala en `bake.js` como el tiburón (una fila de
  fotogramas por rumbo) y dale un personaje, un recorrido y estados en
  `make_package.py`.
- **La portada:** `coverTexture()` en `sea.js` pinta la portada del juego,
  la ventana de la tarjeta (`card/window.png`), que MUN muestra como la
  etiqueta del disco: [la portada del juego](#la-portada-del-juego).

## La portada del juego

La ventana de la tarjeta, `card/window.png`, es la portada del juego de la
tarjeta. Reef pinta la de MUN Collect, con su nombre: el juego de ejemplo de
MUN OS, el que una consola del laboratorio viste con el arrecife salvo que
se le diga otro (`./mun dev shape` pone el paquete en una copia de la
tarjeta de MUN Collect; `--base` nombra otro juego). Solo es correcta
mientras la tarjeta ejecuta MUN Collect. Para una tarjeta con otro juego,
sustitúyela:

- **En una copia del paquete** (`./mun card shape init CARPETA --example
  reef`), sin hornear: pon la portada de tu juego en `card/window.png` (un
  PNG de hasta 1024 × 1024; mira `card` en [MUN Shape](../../../docs/shape.md),
  en inglés), o quita `window` del bloque `card`, y la consola mostrará la
  portada que nombra el manifiesto de la tarjeta. Después,
  `./mun card shape check CARPETA`.
- **En el estudio**, para mantener el aspecto del arrecife: cambia el
  título y los colores que pinta `coverTexture()` en `sea.js`, hornea y
  vuelve a hacer el paquete.

Los nombres MUN y MUN Collect no están bajo la licencia
([`NAME-AND-LOGO.txt`](../../../NAME-AND-LOGO.txt), en inglés): una tarjeta
de otro juego no lleva el de MUN Collect.

## Qué se ejecuta dónde

Lo que muestra una consola es el paquete reproducido por el reproductor de
MUN Shell: imágenes renderizadas de antemano, movidas en pasos fijos al
ritmo de la escena (20 por segundo), dibujadas con el aspecto de píxel de
640 × 360 ampliado, detrás de Home, y respondiendo a los eventos de la
consola (Jugar en foco, Jugar pulsado, una tarjeta que llega, la vuelta de
un juego, la tarjeta retirada). Las capturas y grabaciones de eso, de una
consola o de MUN Shell construido desde este código, son lo que demuestra
que funciona; di de cuál se trata cuando enseñes una.

La escena en un navegador, como la renderiza `bake.html` para hornearla, o
cualquier página web hecha con `sea.js`, es otra cosa: three.js dibujando la
escena 3D en directo, cada fotograma a partir de la geometría, al ritmo y
tamaño del navegador, sin el reproductor de la consola, sus pasos, su
aspecto, Home ni sus eventos. Sus peces siguen los recorridos de la propia
escena, no los del paquete, y su luz, su arena y su profundidad se dibujan,
no se hornean. Una página web que la muestra enseña la escena con la que se
hace el arrecife, no MUN OS en funcionamiento: dilo donde se muestre.

## Llevarlo a otro sitio

Fuera de aquí, el arrecife se usa desde una exportación de un commit
([exportar un paquete](../../../docs/shape.md#exporting-a-package), en
inglés):

```sh
./mun card shape export examples/shape/reef OUT \
  --sources examples/shape/reef-studio \
  --sources examples/shape/sea/world/caustics.png --sources examples/shape/sea/world/bubble.png
```

`OUT/reef-<commit>/` contiene el paquete, estas fuentes (con sus rutas en el
repositorio, para que `sea.js` siga encontrando las dos texturas que toma
prestadas de `sea`), los avisos del repositorio y `export.json`, que
registra el commit y lista cada archivo con su SHA-256. Quien lo usa fija
la versión por el SHA-256 de `export.json`, comprueba la exportación contra
él en sus propias pruebas (`./mun card shape verify-export --expect
SHA256`) y solo cambia de versión exportando de nuevo: nada se copia a mano
y nada sigue una rama. Si el commit está en una rama publicada lo decide
quien publica; la exportación no lo afirma.

## A qué se atiene el horneado

- Cada imagen se renderiza entera a 640 × 360 y se recorta, para que los
  píxeles y el ajuste de los vértices coincidan de una imagen a otra.
- Cada imagen tiene el tamaño que tiene donde reposa lo que muestra; la
  consola solo la escala cuando un recorrido la lleva más lejos o más cerca.
- Los planos sobrepasan la pantalla tanto como deriva la vista. El suelo de
  cada franja acaba en seco donde empieza la siguiente y empieza debajo de
  la más cercana, para que la deriva nunca abra un hueco; la luz de la arena
  se corta igual.
- Lo que se mueve se dibuja aparte de los planos, para que pueda pasar
  delante y detrás de otras cosas según su profundidad.

Todo es obra propia de este repositorio, bajo su licencia
([licencias](../../../docs/licensing.md), en inglés), salvo la aleatoriedad
con semilla de `sea.js`, mulberry32, de Tommy Ettinger (2017), que la
dedicó al dominio público; three.js (MIT) lo instala npm al hornear y no se
incluye aquí. La ventana de la tarjeta lleva el nombre de MUN Collect, que
no está bajo la licencia ([la portada del juego](#la-portada-del-juego)).
