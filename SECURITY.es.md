# Seguridad

[English](SECURITY.md) · **Español**

MUN™ OS es experimental: una imagen de desarrollo para QEMU, no una versión
con soporte, y sin hardware. Aun así tiene fronteras de seguridad que deben
sostenerse, y los avisos sobre ellas son bienvenidos.

## Avisar de una vulnerabilidad

Avisa en privado, no en un issue público:

- en GitHub, **Security → Report a vulnerability** en este repositorio, o
- por correo a **hello@playmun.com**, con «security» en el asunto.

Di a qué afecta (un commit o una versión preliminar, y el `build_id` de
`BUILD-INFO.json` si hay una imagen de por medio), cómo reproducirlo y qué
permite hacer. El mantenedor responde y te mantiene informado; la corrección
entra en `dev`, después en `main` y en la siguiente versión preliminar. Dale
ese tiempo antes de contárselo a nadie más, y di si quieres que se te
reconozca.

## Qué cubre

Lo que MUN OS se compromete a proteger:

- **Las Game Cards como entrada no fiable**: la validación del servicio de
  tarjetas, las reglas del manifiesto y de las rutas, las portadas, las
  partidas y la herramienta de tarjetas que las lee (`services/mun-cardd`,
  `tools/mun-card`).
- **El aislamiento del juego**: lo que puede alcanzar la unidad de un juego,
  el control que el lanzador tiene sobre ella y las partidas que escribe para
  él (`services/mun-launchd`, [ejecutar un juego](docs/runtime.md), en
  inglés).
- **La separación de privilegios**: el shell sin privilegios y los pocos
  ayudantes que puede llamar (`services/mun-shell`).
- **Las descargas**: la verificación de una versión que hace `./mun get`
  contra su `release.json`, y cómo trata las Game Cards que ya existen
  (`vm/bundle.py`).

Fuera: la seguridad de QEMU y del propio ordenador anfitrión, el acceso de
desarrollo del laboratorio a un invitado (qemu-guest-agent, con root por
diseño en la imagen de desarrollo) y la denegación de servicio por una
tarjeta o un juego que el jugador decidió insertar.

## Versiones con correcciones

Solo la última versión preliminar, `main` y `dev`. Las versiones preliminares
anteriores no reciben correcciones.
