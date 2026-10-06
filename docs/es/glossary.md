# Glosario

[English](../glossary.md) · **Español**

Términos que usan la documentación y el código de MUN™.

| Término | Significado |
| --- | --- |
| BOP | Buy. Own. Play. (Compra. Posee. Juega.) Una Game Card legítima basta para poseer su juego y jugarlo sin conexión; las cuentas son opcionales |
| MUN | La consola y el proyecto |
| MUN -1 | La primera generación de consola física, con una sola configuración de hardware con soporte oficial, aún no elegida |
| MUN OS | El sistema operativo de la consola. Por ahora solo existe una imagen de desarrollo para QEMU |
| MUN Shell | La interfaz de la consola, `mun-shell` |
| Game Card | Soporte extraíble que guarda un juego, su presentación y sus partidas ([game-cards.md](../game-cards.md), en inglés). En el laboratorio, una imagen ext4 en bruto que se conecta en caliente a un invitado de QEMU. En español es femenino: «la Game Card» |
| Manifiesto | El descriptor de la tarjeta, en su raíz: `mun.toml` (o `neptune.toml` en una tarjeta anterior); se valida como entrada no fiable |
| Generación de nombres | Las tarjetas anteriores llevan `neptune.toml` y guardan como `neptune-save/1`; las actuales, `mun.toml` y `mun-save/1`. La consola lee las dos y escribe en cada tarjeta la suya ([game-cards.md](../game-cards.md#naming-generations), en inglés) |
| Inserción | Una inserción de una tarjeta, identificada por un valor aleatorio que crea el servicio de tarjetas; cada sesión está ligada a una |
| Tarjeta activa | La única tarjeta que la consola usa a la vez, la primera que se insertó; una segunda espera |
| Retirada segura | Liberar una tarjeta antes de sacarla: ninguna sesión la usa, las escrituras han terminado y se desmonta de forma estricta (en la consola, *Retirar con seguridad*) |
| Sesión | Una ejecución de un juego desde una tarjeta, supervisada por el lanzador, con un resultado |
| Perfil de ejecución | Lo que un juego puede usar y debe aportar: `linux-arm64-v0` (framebuffer) o `linux-arm64-gl-v0` (DRM, OpenGL, audio) ([runtime.md](../runtime.md), en inglés) |
| Envoltorio de la partida | El archivo JSON que la consola escribe en la tarjeta alrededor de la partida de un juego ([saves.md](../saves.md), en inglés) |
| Partida de directorio | Una partida que la consola hace con los archivos que el propio juego escribe, declarados como unidades en el manifiesto |
| Unidad de partida / punto de guardado | Una unidad es un archivo que la tarjeta declara completo por sí mismo, con una comprobación de que lo está; un punto de guardado es un instante que la consola puede verificar (el juego congelado, ninguna unidad abierta para escribir, todas completas), el único en que copia unidades a la tarjeta |
| Laboratorio | El entorno de desarrollo, pruebas e inyección de fallos con QEMU, en `vm/`; no es una plataforma de producto |
| Constructor (builder) | Una VM de QEMU desechable que construye una imagen de MUN OS y se borra después |
| Invitado de imagen | Un invitado de QEMU que arranca desde una build de desarrollo, sin interfaz de red |
| BUILD-INFO | El registro, legible por máquina, de una build: fuentes, paquetes, herramientas de compilación, configuración y huellas ([os/README.md](../../os/README.md), en inglés) |

## Las palabras de la consola

Esta sección solo está en la versión española. La consola arranca en inglés;
en *Settings* › *Account and language* › *Language* se elige el español, y
desde entonces los menús muestran estas etiquetas. La documentación en
español usa las de la columna derecha.

| En inglés | En español |
| --- | --- |
| Game Card | Game Card |
| My games | Mis juegos |
| Settings | Configuración |
| Turn off | Apagar |
| Play | Jugar |
| Eject safely | Retirar con seguridad |
| Ejecting… | Retirando… |
| You can remove the Game Card | Puedes retirar la Game Card |
| Slot empty | Ranura vacía |
| Reading the Game Card… | Leyendo la Game Card… |
| Card not valid | Tarjeta no válida |
| Session ended | Sesión terminada |
| OK | Aceptar |
| Select / Back | Seleccionar / Atrás |
| Offline | Sin conexión |
| Account and language / Language | Cuenta e idioma / Idioma |
| Picture and sound | Imagen y sonido |
| Resolution | Resolución |
| System sounds | Sonidos del sistema |
| Startup sound | Sonido de arranque |
| Auto power off | Apagado automático |
| Network / System | Red / Sistema |
| Reset settings | Restablecer ajustes |

## Lo que no se traduce

Las órdenes y sus opciones (`./mun play --card collect`), las rutas y los
nombres de archivo (`.local/gamecards/`), los campos de JSON y TOML, los
códigos de error, los identificadores y los nombres de producto (MUN, MUN OS,
MUN Shell, MUN Collect). Lo que imprime una herramienta se cita en el idioma
en que lo imprime: los mensajes de `mun-card` (`./mun card`) están en
español y su ayuda (`--help`) en inglés; `./mun get`, `./mun play`,
`./mun dev` y QEMU, en inglés.
