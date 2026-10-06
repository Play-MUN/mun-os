# Ejemplos

[English](README.md) · **Español**

Juegos que se ejecutan desde una Game Card, y recetas para integrar uno. La
construcción de la imagen compila los dos juegos de ejemplo contra las
bibliotecas de la propia imagen y los deja en
`.local/mun/builds/<name>/games/`, junto a la imagen, nunca dentro
([os/README.md](../os/README.md), en inglés).

- [`mun-collect`](mun-collect/README.es.md): C11, framebuffer y evdev,
  estático; un pequeño juego de prueba que pide a la consola guardar en la
  tarjeta (`linux-arm64-v0`).
- [`mun-gl-probe`](mun-gl-probe/README.md) (en inglés): SDL2, OpenGL y
  OpenAL, enlazado contra las bibliotecas de la imagen; una prueba del perfil
  `linux-arm64-gl-v0` y del acceso al contenido durante la partida.

- [`shape`](shape/README.es.md): dos paquetes de MUN Shape, `sea` y
  `paper`, dibujados y sintetizados por un script de aquí; el mismo contrato,
  dos identidades distintas ([docs/shape.md](../docs/shape.md), en inglés).

Un juego que este repositorio no lleva, como la adaptación de uno que posees,
se compila a partir de una receta que guardas fuera
(`./mun dev build --recipe DIR`,
[recetas de juegos](../os/README.md#game-recipes), en inglés) y va en una
tarjeta que haces tú; sus datos, su binario y su imagen de tarjeta se quedan
en `.local/`.

Lo que un juego puede esperar de la consola está en
[docs/runtime.md](../docs/runtime.md) y [docs/saves.md](../docs/saves.md), en
inglés.
