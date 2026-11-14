# Paquetes de ejemplo de MUN Shape

[English](README.md) · **Español**

Dos paquetes para el mismo contrato, [MUN Shape](../../docs/shape.md) (en
inglés), con identidades tan distintas como permite el formato. La consola
dibuja los dos con el mismo código, mundos y transiciones incluidos; las
regresiones de comportamiento del shell y el laboratorio los usan, y un
editor de juegos puede partir de ellos.

| | `sea` | `paper` |
| --- | --- | --- |
| Paleta | placas azul profundo, texto claro, un acento dorado | placas crema, texto de tinta, un acento bermellón |
| Objeto de la tarjeta | contorno orgánico, una ventana al mar | contorno de tarjeta, un sol rojo sobre colinas de tinta |
| Mundo | un degradado iluminado, dos relieves a la deriva con algas, un banco de peces, burbujas que suben, rayos de luz y cáusticas, 20 fps | papel con grano y fibras, dos cordilleras de tinta, motas a la deriva, pétalos que caen, sin luz, 10 fps |
| Superficies | cristal | papel |
| Transición | marea de entrada y de salida, 3,2 s | barrido de entrada, fundido de salida, 1,6 s |
| Sonidos | burbujas, una campanilla, un oleaje con burbujas al insertar | toques de papel, una pincelada, cuerdas pulsadas al insertar |
| Plan de contraste | `bridge`: las placas pasan por el negro | `cut`: un texto oscuro sobre placas claras no puede fundirse desde el texto claro de MUN |

```sh
./mun card shape check examples/shape/sea --report
./mun card shape check examples/shape/paper --report
./mun card shape init .local/mypkg --example paper     # una copia para cambiarla
./mun dev shape examples/shape/sea --window            # el ejemplo en una consola del laboratorio
afplay examples/shape/sea/sfx/insert.wav               # macOS; aplay en Linux
```

De una copia a una tarjeta tuya:
[vestir la consola con tu juego](../../docs/es/guides/shape-your-game.md).

Un tercer ejemplo, `reef` (Reef — ejemplo de MUN Shape), es una plantilla del movimiento de Shape
(`mun-shape/1.1`, un borrador: [docs/shape-motion.md](../../docs/shape-motion.md),
en inglés): una escena marina construida en código, horneada en 3D y puesta
en movimiento por la consola, con bancos de peces que giran en profundidad,
un tiburón, cangrejos, medusas, algas, rayos, luz en la arena, la vista a
la deriva y un banco que responde a Jugar.
[`reef-studio`](reef-studio/README.es.md) es la escena y el horneado que lo
hacen; parte de cualquiera de los dos (`./mun card shape init DIR --example reef`).
El arrecife es el ejemplo oficial del movimiento de MUN Shape: el
reproductor de la consola lo reproduce como cualquier paquete, el estudio
es su fuente y `reef` solo lo escribe el estudio; para usarlo en otro sitio
se toma entero de un commit ([tres partes](reef-studio/README.es.md#tres-partes)).
Solo la v0.1.0-dev.5 o una consola construida desde este código reproduce
el movimiento, y esa versión, o la descarga de una de esas consolas
(`./mun dev bundle`), trae MUN Collect vestido con el arrecife (la tarjeta
`collect-reef`); las versiones preliminares hasta la v0.1.0-dev.4 muestran
el aspecto v1 de reef, su póster fijo
([probarlo](../../docs/shape-motion.md#trying-it), en inglés).

Cada imagen y cada sonido de `sea` y `paper` los hace
[`generate.py`](generate.py) con la biblioteca estándar de Python y semillas
fijas: degradados, siluetas a partir de sumas de senos de periodo entero
(para que las capas en movimiento encajen al repetirse), sprites
sobremuestreados, y síntesis aditiva, de ruido filtrado y de cuerda pulsada
para los sonidos. Las imágenes de reef las renderiza su estudio a partir de
una escena construida en código (con las cáusticas y la burbuja de `sea`), y
sus sonidos son los de `sea`. Todo ello es obra propia de este repositorio,
bajo su licencia ([licencias](../../docs/licensing.md), en inglés); la
página del estudio dice qué hay en él que no lo es.

```sh
python3 examples/shape/generate.py            # rehace los dos paquetes
python3 examples/shape/generate.py --check    # compara con los archivos de aquí
```

`--check` compara píxeles decodificados en lugar de los bytes de los PNG,
porque otro zlib puede comprimir los mismos píxeles de otra manera; las
pruebas rehacen del mismo modo los archivos pequeños.
