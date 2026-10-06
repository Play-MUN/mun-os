# Traer un juego a MUN

[English](../../guides/port-a-game.md) · **Español**

Lo que hace falta para que un juego funcione en MUN™, más allá de ponerlo en
una tarjeta ([crear una Game Card](create-game-card.md) cubre esa parte). Es
para quien tiene el código fuente del juego, o puede compilarlo: MUN ejecuta
programas hechos para él, y nada más.

## Qué es un juego de MUN

Un **programa Linux para AArch64**, para uno de los dos perfiles de ejecución
de la consola, que la consola arranca aislado, sin conexión, con el contenido
y las partidas de la tarjeta como dicen los contratos
([ejecutar un juego](../../runtime.md), en inglés):

| | `linux-arm64-v0` | `linux-arm64-gl-v0` (provisional) |
| --- | --- | --- |
| Para | un juego que dibuja en el framebuffer y lee la entrada de evdev | un juego con SDL2, OpenGL y OpenAL |
| Se enlaza con | nada, o solo las bibliotecas de C del propio sistema (lo más sencillo es estático) | las bibliotecas que trae la imagen: SDL2, OpenAL Soft, tinyxml2, Mesa (GL, EGL, GBM), ALSA, libstdc++ |
| Pantalla | el framebuffer, al tamaño que fijó el núcleo al arrancar | la resolución de la consola, la vigente cuando arranca el juego (`MUN_DISPLAY_MODE`) |
| Tarjeta | `./mun card create … --variant game` | `./mun card create … --variant game-gl` |

Un programa de Windows o de macOS, o uno de Linux para x86_64, no es un juego
de MUN, ni en una tarjeta ni en ningún otro sitio: hay que construirlo de
nuevo. Los motores y las bibliotecas que la imagen no trae deben enlazarse en
el juego de forma estática: el `content/` de la tarjeta se monta con
`noexec`, así que nada de lo que hay ahí se ejecuta ni se carga como código;
solo contiene datos. En la imagen de desarrollo OpenGL se dibuja por
software: cuesta CPU, y un juego debe marcar su propio ritmo.

## Quién necesita qué

- **Los jugadores** necesitan una consola MUN, o la virtual
  ([primeros pasos](../getting-started.md)), y la tarjeta. Nada que construir.
- **Quien trae el juego** necesita construirlo para Linux ARM64 contra las
  bibliotecas de la consola, hacer la tarjeta y probarla en la consola
  virtual: [construir una imagen](../compatibility.md#construir-una-imagen) y
  [hacer tarjetas](../compatibility.md#hacer-game-cards) dicen dónde funciona.

## Construirlo contra las bibliotecas de la imagen

Un juego del perfil GL debe usar las mismas versiones de las bibliotecas que
tiene la consola. Lo fiable es construirlo donde se construye la imagen, a
partir de una **receta**: un directorio que guardas fuera de este repositorio
con `recipe.json` (el nombre y la licencia del juego, las fuentes en commits
fijados, los parches con sus huellas) y un `build.sh` que lo compila:

```sh
./mun dev build --name mygame --recipe ~/recipes/mygame
```

El juego sale en `.local/mun/builds/mygame/games/mygame/`, junto a la imagen,
y el `BUILD-INFO.json` de la construcción registra la receta
([recetas de juegos](../../../os/README.md#game-recipes), en inglés). Una
receta lleva instrucciones y parches, nunca los datos del juego, y MUN OS
nunca lleva un juego de terceros: tu adaptación sigue siendo tuya.

## Sus datos

Los datos que lee el juego, la parte del juego que aporta su dueño, van en la
tarjeta, en `content/`, en solo lectura durante la partida; el juego los
encuentra en `MUN_CONTENT_DIR`
([el contenido durante la partida](../../runtime.md#content-during-play), en
inglés):

```sh
./mun card create mygame --variant game-gl --game .local/mun/builds/mygame/games/mygame/mygame \
    --content ~/mygame-data --title "My Game" --id org.example.mygame --version 1.0.0
```

La tarjeta, con los datos dentro, se queda en `.local/`. No hagas nunca
commit de datos de juego que no tengas derecho a distribuir.

## Sus partidas

Las partidas viven en la tarjeta, nunca en la consola
([partidas](../../saves.md), en inglés). Hay dos formas, que se eligen en el
manifiesto:

- **Partidas de un objeto**: el juego lee su partida de `MUN_SAVE_FILE` y
  pide a la consola que escriba una nueva por `MUN_SAVE_SOCKET`; la consola
  la escribe en la tarjeta de forma atómica y avisa cuando ya está a salvo
  ([partidas de un objeto](../../saves.md#single-object-saves), en inglés).
  MUN Collect guarda así.
- **Partidas de directorio**, para un juego que ya escribe sus propios
  archivos de partida: la tarjeta declara qué archivos del directorio del
  juego son partidas y cómo reconocer uno completo (`--saves-directory`,
  `--saves-unit PATTERN:CHECK`, `--saves-max-bytes`), y la consola los copia
  a la tarjeta, comprobados, mientras el juego corre y cuando termina
  ([partidas de directorio](../../saves.md#directory-saves), en inglés).

Nada más de lo que escribe el juego se guarda: su directorio, `/tmp` y
`/var/tmp` desaparecen con la sesión
([lo que recibe el juego](../../runtime.md#what-the-game-gets), en inglés), y
un juego no debe contar con nada que deje en otro sitio.

## Probarlo, y después el camino completo

En la consola virtual: inserta la tarjeta, juega, guarda, termina el juego,
retira la tarjeta, reinicia la consola y continúa
([crear una Game Card](create-game-card.md#5-jugar-y-guardar) lo recorre). El
resultado de la sesión dice cómo terminó el juego; el diario del lanzador
(`./mun dev vm GUEST launchd-log`) dice por qué no arrancó. Retira también la
tarjeta con el juego en marcha: el juego termina y las partidas ya escritas
están a salvo ([retirada y fallos](../../saves.md#removal-and-failures), en
inglés).

## Lo que aún no hay

Mandos (por ahora solo teclado), actualizaciones del contenido de una
tarjeta, otros perfiles de ejecución y hardware físico: véase la
[arquitectura](../../architecture.md) (en inglés). El perfil GL es
provisional: su lista de bibliotecas registra versiones, no promete una ABI.
