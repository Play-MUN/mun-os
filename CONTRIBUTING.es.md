# Contribuir

[English](CONTRIBUTING.md) · **Español**

MUN™ OS está en una fase temprana: una imagen de desarrollo para QEMU,
ninguna versión con soporte y ningún hardware elegido. Las contribuciones son
bienvenidas: correcciones, pruebas, documentación, herramientas de tarjetas y
de juegos, y llevar el laboratorio a más ordenadores. Lee primero el
[README](README.es.md), los [primeros pasos](docs/es/getting-started.md) y la
[arquitectura](docs/architecture.md) (en inglés); los contratos que un cambio
debe respetar están en [docs/](docs/es/README.md).

## Cómo ayudar

No toda la ayuda es código. Cada una de estas formas pasa por una incidencia
o una pull request contra `dev`, como explica el resto de esta página:

- **Pruébalo en tu ordenador.** Sigue los [primeros pasos](docs/es/getting-started.md)
  donde las tablas dicen sin probar, o comprobado solo sin ventana: Linux o
  Windows con ventana, teclado y sonido, Linux ARM64 con KVM, otra
  distribución. Cuenta lo que pasó con la plantilla de errores (tu
  ordenador, las versiones de QEMU y de Python, el build id de la imagen);
  saber que funciona vale tanto como saber que no.
- **Errores e instrucciones confusas.** Un paso que no hace lo que dice la
  página, o que tuviste que adivinar, es un error de la documentación: abre
  una incidencia, o una pull request que corrija la página, en inglés y, si
  la tiene, en su traducción.
- **Traducciones y accesibilidad.** Las páginas en español se mantienen al
  día con sus originales en inglés ([abajo](#documentación-y-traducciones)).
  Revisarlas frente a las etiquetas de la propia consola, o hacer la
  interfaz más fácil de leer (contraste, tamaños, textos que no caben), ayuda
  a todos los jugadores. Un idioma nuevo empieza como propuesta en una
  incidencia.
- **Paquetes de MUN Shape y juegos de ejemplo.** Un paquete con tu propio
  arte, comprobado con `./mun card shape check DIR --report` (por ejemplo,
  `./mun card shape check examples/shape/sea --report`), enseña lo que
  puede hacer MUN Shape; un juego de ejemplo enseña lo que puede llevar una
  tarjeta. Aporta obra tuya, o material que tengas derecho a aportar con la
  licencia del proyecto, y di de dónde viene lo que no sea tuyo
  ([derechos](#derechos-y-firma)). Los ejemplos de `examples/shape/` son obra
  propia de este repositorio, generada con código.
- **Servicios, herramientas e integración de hardware.** El shell, el
  servicio de tarjetas, el lanzador, las herramientas de tarjetas y el
  laboratorio: la [arquitectura](docs/architecture.md) y los
  [lenguajes](docs/development/languages.md), en inglés, explican cómo
  encajan. La integración de hardware (arranque, pantalla, entrada, el lector
  de tarjetas) corresponde a la configuración oficial cuando se elija, o a un
  fork para otra placa; propónla antes en una incidencia.

### Primeras tareas

- Sigue los primeros pasos en Linux o en Windows con ventana y sonido, y
  cuenta lo que viste en cada paso.
- Ejecuta `./mun play` en un ordenador ARM64 con Linux y KVM: las
  herramientas lo admiten y nadie lo ha probado todavía.
- En español y a 1080p, *Configuración* › *Imagen y sonido* corta la fila
  *Sonidos del juego en los menús*; haz que se vea entera sin cambiar la
  etiqueta.
- Lee una guía como alguien que llega de nuevas y convierte en una
  corrección cada paso que tuviste que adivinar.
- Una más grande: un tercer ejemplo de MUN Shape, con el arte y los sonidos
  generados con código como en `examples/shape/generate.py`, que dé `LISTO`
  al comprobarlo.

## Quién decide

MUN OS lo fundó y lo mantiene Iván Moreno Mendoza (Play MUN). El mantenedor
revisa las pull requests y decide qué entra en `dev` y, desde ahí, en la
versión oficial, `main` y sus releases; una bifurcación puede, por supuesto,
seguir su propio camino ([licencias](docs/licensing.md#the-mun-and-play-mun-names-and-logos),
en inglés, dice cómo puede llamarse).

## Antes de empezar

- **Correcciones pequeñas y documentación**: abre una pull request
  directamente.
- **Cualquier cosa mayor** (una función nueva, un cambio de comportamiento
  que una tarjeta, una partida o un juego puedan notar, una dependencia o un
  componente nuevos): abre antes un issue y describe el problema y la
  propuesta, para hablarlo antes de que dediques tiempo. Los cambios de
  arquitectura se acuerdan en el issue antes de implementarlos.
- Los problemas de seguridad van en privado al mantenedor, no a un issue
  ([seguridad](SECURITY.es.md)).

## Preparación y comprobaciones

Los requisitos para jugar y para construir están en
[compatibilidad](docs/es/compatibility.md). Las comprobaciones del anfitrión
solo necesitan Python 3.9 o posterior y Make; con un compilador de C se
añaden las pruebas de partidas del juego de ejemplo.

```sh
make check      # enlaces, anclas y traducciones de la documentación, sintaxis de Python, regresiones del anfitrión, pruebas de partidas en C
make test       # solo las regresiones de Python del anfitrión
```

`make check` no necesita red ni arranca ninguna VM; las pull requests lo
ejecutan solas en Linux y en macOS. No construye el shell ni una imagen: eso
lo hace `./mun dev build`, y un cambio en un servicio, en la composición de la
imagen o en el laboratorio debe probarse también en un invitado
([laboratorio](vm/README.md), en inglés). Di en la pull request qué
comprobaciones ejecutaste y cuáles no pudiste.

[Lefthook](https://lefthook.dev/) ejecuta `make check` antes de cada commit
si está instalado en el checkout (`lefthook install`); `lefthook-local.yml`
está ignorado y puede ampliarlo en tu copia.

## Cambios

- Cada cambio, con un solo propósito, y sin romper el camino de juego
  (insertar, jugar, guardar, retirar, continuar).
- **No rompas nunca las Game Cards existentes, sus partidas ni los juegos que
  llevan.** Un nombre o un comportamiento del que dependen tarjetas, partidas
  o juegos solo cambia con una versión nueva de su contrato, respetando la
  anterior.
- Trata el contenido de las tarjetas y las rutas de los soportes extraíbles
  como entrada no fiable, y los datos no válidos y los dispositivos
  desconectados como errores recuperables.
- Mantén el shell sin privilegios y separado de las operaciones de
  dispositivos y montajes.
- Nada del juego sin conexión puede depender de una cuenta ni de un servicio
  de red.
- Documenta los contratos de propiedad, vida, errores, hilos y ABI en las
  fronteras nativas ([lenguajes](docs/development/languages.md), en inglés).
- Explica en comentarios las razones que no son evidentes; no repitas el
  código.
- Distingue lo medido en hardware, lo observado en una VM, lo que muestra un
  simulacro y lo que es una hipótesis.

## Pruebas

Las pruebas del anfitrión están en `tests/` y usan `unittest` de Python, solo
con la biblioteca estándar, archivos temporales y fronteras de procesos y
protocolos simuladas; no necesitan red, QEMU ni root
([tests/README.md](tests/README.md), en inglés). Añade una prueba de regresión
con cada corrección. Las regresiones de comportamiento del shell se ejecutan
sobre su binario compilado en cada construcción de la imagen
(`./mun dev build`,
[services/mun-shell/tests/behaviour.py](services/mun-shell/tests/behaviour.py)).
Lo que solo puede verse en un invitado en marcha se describe en la pull
request con las órdenes usadas.

## Documentación y traducciones

La documentación se escribe en inglés, que es la referencia técnica. Algunas
páginas tienen además una traducción al español: `docs/es/` reproduce
`docs/`, una página de otro sitio tiene `NAME.es.md` junto a `NAME.md`, y
cada pareja se enlaza en los dos sentidos con una línea English / Español.
`docs/translations.json` lista cada traducción con la SHA-256 del texto
inglés contra el que se revisó por última vez.

Si cambias una página que tiene traducción, pon al día la traducción en el
mismo cambio si puedes, o di en la pull request que hace falta. `make check`
falla hasta que la traducción se ha revisado contra el texto inglés nuevo y su
`source_sha256` tiene el valor que imprime el control; ponlo solo después de
leer las dos. En una traducción, las órdenes, opciones, rutas, nombres de
archivo, campos de JSON y TOML y códigos de error se quedan como están, y la
salida de una herramienta se cita en el idioma en que la imprime.

## Ramas, commits y pull requests

Un cambio recorre este camino:

1. Una rama de vida corta desde `dev`.
2. Una pull request contra `dev`, con sus comprobaciones correctas, revisada
   y aprobada por el mantenedor.
3. La integración en `dev`, donde los cambios esperan hasta que se prepara
   una versión preliminar.
4. Una pull request de `dev` a `main`, la comprobación del candidato como
   dice [publicación de versiones](docs/releasing.md) (en inglés) y la
   release etiquetada en `main`.

En detalle:

- `dev` es donde entra primero el trabajo. Haz una bifurcación del
  repositorio o, con permiso de escritura, trabaja en una rama de vida corta
  desde `dev`: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- `main` guarda lo publicado: recibe `dev` por una pull request, integrada
  con un commit de unión para que las dos conserven una sola historia, cuando
  se prepara una versión preliminar, y las releases son etiquetas sobre
  commits probados de `main`. Los cambios solo llegan a una u otra rama por
  pull requests con las comprobaciones correctas.
- Usa [Conventional Commits](https://www.conventionalcommits.org/) y tu
  propio nombre y correo en Git: tu contribución sigue siendo tuya.
- Firma cada commit (`git commit -s`, abajo).
- Revisa `git diff --cached` antes de cada commit y no reescribas historia
  que tengan otros.
- Abre la pull request contra `dev` (GitHub propone `main`, la rama que
  reciben los jugadores: cambia la base), rellena su plantilla y mantenla
  centrada; las pull requests a `dev` se integran aplastadas (squash).

## Derechos y firma

La obra propia de MUN OS tiene licencia [Apache License 2.0](LICENSE), y
también las contribuciones: como dice la sección 5 de la licencia, lo que
envías con intención de que se incluya queda bajo sus términos, sin
condiciones adicionales, salvo que digas expresamente lo contrario. Tu
contribución sigue siendo tuya: la licencias, no la cedes.

La línea `Signed-off-by:` que añade `git commit -s` certifica el
[Developer Certificate of Origin 1.1](https://developercertificate.org/): que
escribiste el cambio o tienes de otro modo derecho a enviarlo con la licencia
del proyecto. Envía solo lo que puedas licenciar así. El código, las
tipografías, las imágenes u otro material de otras fuentes conservan sus
propios términos: di de dónde vienen y con qué licencia, para que
[licencias](docs/licensing.md) (en inglés) pueda listarlos. Los nombres y
logotipos de MUN y Play MUN no se licencian.

El texto que vale es el de la licencia, en inglés (`LICENSE`); este resumen
no lo sustituye.

## Lo que queda fuera de Git

Todo lo generado vive en `.local/`, que Git ignora: builds, invitados,
imágenes de tarjeta, claves y registros. Este repositorio no lleva ningún
juego de terceros: la adaptación de un juego que posees va en una receta que
guardas fuera ([traer un juego a MUN](docs/es/guides/port-a-game.md)), con
los términos de ese juego. No hagas nunca commit de datos de juego que no
tengas derecho a distribuir, de instaladores comerciales, de credenciales ni
de configuración personal de tus herramientas; esta última, mejor en
`.git/info/exclude` o en tu archivo de ignorados global que en `.gitignore`.

## Releases

Cómo se hace una versión preliminar, y qué debe llevar, está en
[publicación de versiones](docs/releasing.md) (en inglés).
