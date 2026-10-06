# Ver MUN Shape en acción

[English](../../guides/see-shape-in-action.md) · **Español**

Cómo se organiza una Game Card que lleva MUN Shape, sea cual sea el juego, y
cómo ver a una consola tomar la identidad de ese juego: arrancar una consola
sin tarjeta, meter la tarjeta, jugar, volver, retirarla y apagar. Para hacer
un paquete propio, sigue [vestir la consola con tu juego](shape-your-game.md);
las reglas están en [MUN Shape](../../shape.md), en inglés.

Esta guía cita los menús de la consola en español; arranca en inglés y se
cambia en *Settings* › *Account and language* › *Language*
([glosario](../glossary.md#las-palabras-de-la-consola)).

## Qué lleva una tarjeta vestida

La consola lee una carpeta: `mun-shape/` dentro de la raíz de contenido de la
tarjeta, la carpeta que nombra el manifiesto. Nada en la consola sabe de qué
juego se trata; la misma lectura sirve para cualquier tarjeta que tenga esa
carpeta.

Un juego pequeño, con su ejecutable en el contenido, hecho con
`./mun card create … --game FILE --shape DIR` (aquí, MUN Collect vestido con
el ejemplo `sea`):

```text
mun.toml                  el manifiesto: identidad, perfil de ejecución, entrada content/mun-collect
cover.png                 la portada
content/
  mun-collect             el juego
  README.txt, assets/ …   los archivos del propio juego
  mun-shape/              MUN Shape: lo que la consola lee para vestirse
    shape.json            formato "mun-shape/1": paleta, tarjeta, mundo, superficies, transición, sonidos
    card/window.png       la pantalla del objeto de la tarjeta
    world/backdrop.png    el mundo detrás de la pantalla principal…
    world/…               … sus capas, sprites y texturas de luz
    sfx/move.wav          los sonidos de los menús…
    sfx/enter.wav
    sfx/back.wav
    sfx/insert.wav        … y el aviso de inserción
saves/                    las partidas, que escribe la consola
```

Un juego más grande cuyos datos son una carpeta, hecho con
`./mun card create … --variant game-gl --content DIR`, donde `DIR` contiene
el juego, sus datos y `mun-shape/`:

```text
mun.toml                  (o neptune.toml en una tarjeta de la generación de nombres anterior)
cover.png
content/
  mygame                  el ejecutable del juego
  data/, gfx/, sfx/, …    los datos del juego, como los espera el juego
  mun-shape/              la misma carpeta, con los mismos archivos que arriba
    shape.json
    card/  world/  sfx/
saves/
```

El paquete está junto a los archivos del juego y no cambia ninguno. Una
tarjeta sin `mun-shape/` se juega con el aspecto de MUN (con los colores que
presta su manifiesto, o una paleta leída de su portada). Un paquete que la
consola no puede usar se descarta bloque a bloque, y la tarjeta sigue siendo
válida.

## La consola tiene que mostrar MUN Shape

Una imagen registra en su `BUILD-INFO.json` el MUN Shape que lee su consola
(`"shape": {"format": "mun-shape/1"}`). Las imágenes construidas desde tu
copia del repositorio lo registran; la v0.1.0-dev.2 publicada no muestra MUN
Shape. Mira lo que tienes:

```sh
./mun dev list        # cada build e invitado: "shape mun-shape/1" o "shape not recorded"
```

Construye una si ninguna lo registra: `./mun dev build`
([construir la imagen tú mismo](../getting-started.md#construir-la-imagen-tú-mismo)).

## Míralo

1. **Una consola sin tarjeta.** Arráncala en una ventana:

   ```sh
   ./mun play                     # el invitado `play`, creado la primera vez a partir de tu imagen más reciente
   ```

   Un invitado conserva la imagen con la que se creó; si esa imagen no
   muestra MUN Shape, crea un invitado nuevo: `./mun play --guest NAME` (y el
   mismo nombre en las órdenes de abajo).

2. **Mete la tarjeta** cuando se vea la pantalla principal, desde otro
   terminal:

   ```sh
   ./mun dev vm play card-attach mygame       # .local/gamecards/mygame.img
   ```

   La tarjeta se lee, el objeto de la tarjeta toma la ventana del paquete, la
   transición trae la identidad desde el objeto hacia fuera y el aviso suena
   una vez. `./mun play --card mygame`, en cambio, mete la tarjeta al arrancar
   la consola: la consola la encuentra ahí y se viste enseguida, sin llegada
   ni aviso, como una consola que se enciende con su tarjeta dentro.

3. **Jugar** (*Game Card* › *Jugar*). El juego arranca enseguida; nada espera
   a una animación.

4. **Volver.** Cuando el juego termina, la identidad ya está puesta: un
   fundido breve, sin llegada ni aviso, incluso bajo el diálogo *Sesión
   terminada*.

5. **Retirar con seguridad** (*Game Card* › *Retirar con seguridad*). La
   identidad se queda hasta que la consola ha liberado la tarjeta; después se
   va con su transición de salida y aparece «Puedes retirar la Game Card»;
   entonces la ventana saca la tarjeta, como lo haría una mano, y la ranura
   queda vacía. Para sacar la tarjeta sin retirarla con seguridad:
   `./mun dev vm play card-detach mygame --abrupt` (la ranura virtual lo
   indica unos segundos después).

6. **Apagar** (*Apagar* › *Apagar*).

Por el camino, prueba *Configuración* › *Imagen y sonido*: *MUN Shape*
(Completo, Solo colores, Desactivado), *Reducir movimiento* (todas las
transiciones son fundidos y el mundo se queda quieto) y los sonidos
(*Sonidos del sistema*, *Sonidos del juego en los menús*). Configuración y los
diálogos siguen siendo de MUN sobre cualquier mundo.

## Qué mirar

- `./mun card inspect mygame` y `./mun card shape check mygame`: lo que la
  consola usará de la tarjeta, bloque a bloque, y por qué se descartaría un
  bloque.
- `./mun dev vm play shell-log`: el diario del shell, con una línea para la
  identidad de cada inserción, su llegada (`comes in (arrival, …)`), su
  vuelta (`comes in (return, fade)`) y su salida (`leaves (release)`).
- Mientras cambias un paquete, `./mun dev shape DIR --watch --window` muestra
  cada cambio en una tarjeta desechable, en una consola propia
  ([la guía, sección 5](shape-your-game.md#5-previsualízalo-en-la-consola)).
