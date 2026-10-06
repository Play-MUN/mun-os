# Documentación

[English](../README.md) · **Español**

Las páginas que todavía no tienen traducción enlazan a su original, en inglés
([lo que sigue en inglés](#lo-que-sigue-en-inglés)).

## Empieza aquí

- [README](../../README.es.md): qué es MUN™ OS y qué funciona hoy.
- [Primeros pasos](getting-started.md): descarga las herramientas y una
  imagen, arranca la consola, juega, guarda y continúa.
- [Compatibilidad](compatibility.md): dónde funcionan jugar, hacer tarjetas
  y construir imágenes, y cómo se comprobó.

## Guías

- [Crear una Game Card](guides/create-game-card.md): MUN Collect en una
  tarjeta propia, desde el ejecutable hasta una partida recuperada tras
  reiniciar.
- [Traer un juego a MUN](guides/port-a-game.md): perfiles de ejecución,
  bibliotecas, datos y partidas, recetas; por qué empaquetar un juego no es
  portarlo.
- [Vestir la consola con tu juego](guides/shape-your-game.md):
  un paquete de MUN Shape desde un ejemplo hasta una tarjeta, comprobado,
  previsualizado en la consola, jugado y retirado.
- [Ver MUN Shape en acción](guides/see-shape-in-action.md): qué
  lleva una tarjeta con MUN Shape, sea cual sea el juego, y una consola que
  toma su identidad desde la inserción hasta el apagado.

## Contratos

- [Game Cards](../game-cards.md) (en inglés): la imagen, el manifiesto, las
  generaciones de nombres, la conversión.
- [Partidas](../saves.md) (en inglés): el almacenamiento en la tarjeta, las
  partidas de un objeto y de directorio, la retirada y los fallos.
- [Ejecutar un juego](../runtime.md) (en inglés): el lanzamiento, el
  aislamiento, los perfiles de ejecución, el entorno, la pantalla, el
  contenido durante la partida, los resultados de la sesión.
- [MUN Shape](../shape.md) (en inglés): el paquete con el que una tarjeta
  viste la consola con la identidad de su juego; su formato, sus límites, sus
  reglas de contraste y su validador.

## Construcción y referencia

- [Construcción de la imagen](../../os/README.md) (en inglés): cómo se compone
  una imagen de MUN OS, qué registra una build, las recetas de juegos, la
  reproducibilidad.
- [Laboratorio](../../vm/README.md) (en inglés): builds y consolas virtuales,
  Game Cards, entrada, registros.
- [Arquitectura](../architecture.md) (en inglés): componentes, fronteras, lo
  que será de la integración del hardware, lo que no está implementado.
- [Lenguajes y fronteras nativas](../development/languages.md) (en inglés).
- [Glosario](glossary.md).

## El proyecto

- [Contribuir](../../CONTRIBUTING.es.md), [seguridad](../../SECURITY.es.md),
  [publicación de versiones](../releasing.md) (en inglés).
- [Licencias](../licensing.md) (en inglés): la licencia de MUN OS, los
  componentes de terceros, las imágenes y sus fuentes, los nombres y
  logotipos de MUN y Play MUN.

Los componentes documentan sus propios protocolos, unidades y privilegios:
[MUN Shell](../../services/mun-shell/README.md),
[servicio de tarjetas](../../services/mun-cardd/README.md),
[lanzador](../../services/mun-launchd/README.md),
[`mun-card`](../../tools/mun-card/README.md) y
[pruebas](../../tests/README.md), en inglés, y [ejemplos](../../examples/README.es.md).

## Lo que sigue en inglés

La versión inglesa es la referencia técnica. La española cubre, por ahora,
el README, esta página, los primeros pasos, el glosario, la compatibilidad,
las guías para crear una Game Card, traer un juego y vestir la consola con
MUN Shape, la de ver MUN Shape en acción, cómo contribuir, la seguridad y los
README de los ejemplos, de MUN Collect y de los paquetes de MUN Shape de
ejemplo. Sigue solo en inglés:

- Las demás páginas de `docs/`: los contratos, el de MUN Shape incluido, la
  arquitectura, los lenguajes, las licencias y la publicación de versiones.
  Aquí aparecen marcadas «en inglés».
- Los README de los componentes, de las herramientas, de las pruebas, del
  laboratorio, de la construcción de la imagen y de la prueba gráfica
  (`mun-gl-probe`).
- Los textos de las licencias (`LICENSE`, `NOTICE`, `NAME-AND-LOGO.txt` y
  las licencias de las tipografías), que son los que valen.
- El código, sus comentarios, los mensajes de los commits y la plantilla de
  las pull requests.
- Los mensajes y la ayuda (`--help`) de `./mun get`, `./mun play` y
  `./mun dev`, y los de QEMU. Los mensajes de `./mun card` están en español
  y su ayuda, en inglés.

La consola arranca en inglés y también habla español
([glosario](glossary.md#las-palabras-de-la-consola)).
