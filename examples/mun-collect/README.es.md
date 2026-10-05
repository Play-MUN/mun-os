# MUN Collect

[English](README.md) · **Español**

Un pequeño juego de prueba que vive en una Game Card: mueve el cuadrado de
tinta con las flechas, recoge los cinco discos de cobre, pulsa S para guardar
tu progreso en la tarjeta y Esc para volver a la consola. Su objetivo es
demostrar el camino de arranque y el de las partidas, no divertir.

- C11, sin bibliotecas: salida por framebuffer (`fb.c`) y entrada por evdev
  (`input.c`), reglas en `game.c`, dibujo y una fuente de 5×7 incrustada en
  `draw.c`.
- Consulta el modo del framebuffer antes de dibujar (resolución, color
  verdadero de 32 bpp, longitud de línea) y rechaza cualquier otro; encuentra
  los teclados por sus capacidades (flechas + Esc), nunca por número de
  dispositivo.
- Sale con 0 al pulsar Esc; con 2 si el framebuffer no sirve; con 3 si no
  encuentra teclado. SIGTERM termina el bucle limpiamente y deja la pantalla
  en blanco.
- Lee `MUN_CARD_ID` (informativo) y `MUN_FRAMEBUFFER` (por defecto
  `/dev/fb0`). Cada variable de la consola se lee primero con su nombre `MUN_`
  y después con su nombre anterior `NEPTUNE_`, así que el mismo código corre
  en consolas anteriores a los nombres MUN; los binarios anteriores solo leen
  `NEPTUNE_`, que la consola sigue publicando para las tarjetas de la
  generación anterior
  ([generaciones de nombres](../../docs/game-cards.md#naming-generations), en
  inglés). Acepta los dos envoltorios de partida, `mun-save/1` y
  `neptune-save/1`; la consola da a cada tarjeta solo el suyo. Sin red.
- Guarda a través de la consola, nunca en la tarjeta directamente
  ([partidas](../../docs/saves.md#single-object-saves), en inglés). Al
  arrancar lee `MUN_SAVE_FILE` (la partida actual que la consola copió en su
  directorio de trabajo):
  - una partida compatible recupera la semilla, así que los discos están
    donde estaban, la posición, los discos recogidos y el tiempo
    transcurrido;
  - sin archivo, empieza una partida nueva;
  - una copia que la consola recuperó de `save.json.prev` se anuncia como
    «COPIA ANTERIOR: n/5»;
  - un archivo dañado o incompatible se anuncia en el marcador y no se
    sobrescribe, salvo que el jugador guarde a propósito.

  **S** pide guardar por `MUN_SAVE_SOCKET`: el marcador muestra
  «GUARDANDO...» mientras la consola escribe y solo entonces «GUARDADO EN LA
  GAME CARD», o «ERROR AL GUARDAR: <causa>» (`NO_SPACE`, `CARD_REMOVED`,
  `SAVE_BUSY`, …). Sin ninguna de las dos variables, el juego se juega pero
  no puede guardar.
- La partida (esquema 1, `save.c`): `{"seed","x","y","elapsed","collected",
  "taken":[0|1 ×5]}`, 64 KiB como máximo según el contrato, unos cientos de
  bytes en la práctica. La consola la envuelve en su envoltorio; el juego
  valida el `format` y el `schema` del envoltorio y la coherencia interna de
  la partida (`collected` debe ser el número de marcas en `taken`) antes de
  fiarse de ella.

La construcción de la imagen lo compila (gcc 14, estático) con las
herramientas de la propia imagen y lo deja junto a ella, nunca dentro:

```sh
./mun dev build --name one
python3 tools/mun-card/mun-card create game --variant game --game .local/mun/builds/one/games/mun-collect/mun-collect
```

La construcción registra el tamaño y la SHA-256 del binario y las
herramientas en `BUILD-INFO.json`; no es una construcción reproducible
verificada. Cualquier sistema Debian 13 arm64 con gcc y make puede
construirlo también con `make`. `make test` (con cualquier compilador de C
del anfitrión) ejecuta `tests/test_save.c`: ida y vuelta, partida truncada,
que no es JSON, formato equivocado, otro esquema, partida incoherente,
anidamiento profundo; el `make check` general lo ejecuta si hay compilador.
