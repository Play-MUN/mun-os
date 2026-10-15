# Qué cambiar y dónde

[English](../contributor-map.md) · **Español**

Un mapa breve para un primer cambio: dónde vive cada parte de MUN OS y cómo
comprobarla. [Contribuir](../../CONTRIBUTING.es.md) dice cómo se proponen los
cambios; la [arquitectura](../architecture.md) (en inglés), cómo encajan las
partes.

## El mapa

| Para cambiar | Mira en | Compruébalo con |
| --- | --- | --- |
| Lo que dice un panel o un diálogo, sus opciones, los menús | `services/mun-shell/qml/Panels.qml` (textos y opciones), `qml/Main.qml` (navegación, teclas, diálogos), `qml/I18n.qml` (los dos idiomas) | la build de la imagen, que ejecuta las regresiones de comportamiento de la shell; capturas en una consola de laboratorio |
| Un ajuste | `services/mun-shell/src/shellsettings.*` (valores, archivo), `qml/Panels.qml` (su sección), la tabla de ajustes del [README de la shell](../../services/mun-shell/README.md#settings) (en inglés) | los casos de comportamiento `settings` y `home` |
| Lo que dibuja la pantalla principal (la red de luz, los objetos, el arranque) | `services/mun-shell/src/` (`backdrop.*`, `heroicon.*`, `bootintro.*`) | las regresiones de comportamiento; fotogramas y tiempos en una consola de laboratorio |
| El formato de las Game Cards, `mun-card` | `tools/mun-card/mun_card/`, [Game Cards](../game-cards.md) (en inglés) | `tests/test_card.py` |
| La lectura de tarjetas, las partidas | `services/mun-cardd/cardd.py`, [partidas](../saves.md) (en inglés) | `tests/test_cardd.py`, `tests/test_directory_saves.py` |
| Empezar y terminar un juego | `services/mun-launchd/launchd.py`, [ejecución](../runtime.md) (en inglés) | `tests/test_launchd.py`, `tests/test_launch_cleanup.py` |
| MUN Shape | [MUN Shape](../shape.md) (en inglés), `tools/mun-card/mun_card/shape*.py`, `services/mun-shell/src/shape*`, `examples/shape/` | `tests/test_shape.py`, `tests/test_cardd_shape.py`, los casos de comportamiento `world` y `memory` |
| Restablecer la consola | `services/mun-reset/` | `tests/test_reset.py` |
| Lo que contiene la imagen | `os/mkosi/` (paquetes, archivos, perfiles), `os/inputs.json` (entradas fijadas) | `tests/test_os.py`, `tests/test_deploy.py` y después `./mun dev build` |
| El laboratorio (`./mun dev`, `./mun vm`) | `vm/`, `mun` | `tests/test_vm.py`, `tests/test_mundev.py` |
| Los juegos de ejemplo | `examples/` | sus propias pruebas (`examples/mun-collect/tests`), una tarjeta hecha con `mun-card` |
| La documentación y sus traducciones | `docs/`, `docs/es/`, los `*.es.md` junto a un original, `docs/translations.json` | `make check` |

## Las comprobaciones

- **`make check`**, en Linux y en macOS: la documentación (enlaces, anclas,
  traducciones revisadas frente a sus originales), la sintaxis de Python y
  las pruebas del anfitrión en `tests/` ([qué cubre cada una](../../tests/README.md),
  en inglés). No necesita QEMU, ni root, ni red.
- **`./mun dev build`**: construye una imagen en un builder desechable y
  ejecuta las regresiones de comportamiento de la shell sobre el binario
  compilado (la shell necesita Qt, que las comprobaciones del anfitrión no
  tienen).
- **Una consola de laboratorio**: `./mun dev run` arranca una build, `./mun
  vm screenshot` captura lo que hay en pantalla y `./mun dev smoke` juega
  una tarjeta de principio a fin ([laboratorio](../../vm/README.md), en
  inglés).

## Tres tamaños que no hay que confundir

| Qué | Cuánto | Dónde |
| --- | --- | --- |
| El repositorio | 8 MiB de archivos, y unos pocos más con el historial | lo que guarda Git |
| El sistema instalado | 700 MiB en el sistema de archivos raíz de la consola; una descarga de unos 450 MB | lo que produce una build de la imagen |
| Los artefactos locales | unos 450 MB por build, más las imágenes en caché del builder, consolas, tarjetas y fuentes | `.local/`, que Git ignora y desde donde nunca se publica nada |
