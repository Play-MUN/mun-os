# MUN Dev: el perfil de la placa de desarrollo (experimental)

[English](../mun-dev.md) · **Español**

MUN Dev es la placa en la que se desarrolla MUN™ OS cuando una consola
virtual no basta: una Radxa Dragon Q6A (Qualcomm QCS6490, gráficos Adreno
643), con una pantalla por HDMI, un teclado, red por cable y su ranura
microSD haciendo de lector de tarjetas. El perfil `q6a-dev` de la
construcción de imágenes hace MUN OS para ella.

**No es MUN.** La consola se diseña alrededor de otra placa, la Radxa
Dragon Q8B, en la que nada de esto se ha ejecutado y de la que nada de esto
dice nada. La imagen dice lo que es (`environment = "radxa-dragon-q6a"`,
`release = false`), la ranura microSD de la placa no es el lector de la
consola, y nada de esta página es una cualificación de hardware. El perfil
es experimental: existe para que el sistema se encuentre con una GPU, una
pantalla, un sonido y una tarjeta reales, y cambia con ese trabajo.

## Qué hay, y qué no

- **El perfil, en código fuente.** Todo lo que hace la imagen está en
  [`os/mkosi/mkosi.profiles/q6a-dev/`](../../os/mkosi/mkosi.profiles/q6a-dev/):
  la imagen la construyes tú.
- **Ninguna imagen para descargar.** Una versión preliminar lleva solo la
  imagen de QEMU. Este perfil instala paquetes que Debian no tiene (el
  kernel de la placa, el firmware de su GPU y de su DSP de audio, su
  topología de audio:
  [`pinned-packages.json`](../../os/mkosi/mkosi.profiles/q6a-dev/pinned-packages.json)
  nombra cada uno con su fuente), parte de ello firmware que no declara
  licencia propia, y `./mun dev sources` no reúne su código fuente
  ([paquetes fijados](../../os/README.md#pinned-packages), en inglés). Hasta
  que eso esté resuelto no se publica ninguna imagen de este perfil.
- **Ninguna promesa de soporte.** Es la placa que tenemos en la mesa, con
  las pantallas y las tarjetas que tenemos.

## Construir e instalar

```sh
./mun dev build --profile q6a-dev --name NOMBRE
```

El resultado es `mun-os-<versión>-radxa-dragon-q6a.img.xz` en
`.local/mun/builds/NOMBRE/`, el disco entero, con su `BUILD-INFO.json`
([construcción de la imagen](../../os/README.md#profiles), en inglés).
Escríbelo en la eMMC de la placa, o en una tarjeta microSD para arrancar
desde la ranura; se reemplaza todo lo que haya en el destino:

```sh
xz -dc mun-os-<versión>-radxa-dragon-q6a.img.xz | sudo dd of=/dev/DISPOSITIVO bs=4M conv=fsync
```

Nosotros escribimos la eMMC desde el sistema del fabricante arrancado de
una tarjeta microSD, y la leemos de vuelta contra la imagen. El firmware de
la placa debe arrancar la eMMC antes que la ranura para que una tarjeta en
la ranura se lea como Game Card y no se arranque. La placa que usamos tiene
el firmware 6.0.251230 en su flash SPI, el que traía; no lo hemos
actualizado. El perfil arranca con una entrada de arranque simple y no con
una imagen de núcleo unificada porque un informe sobre un firmware posterior
(6.0.260120) dice que este se reinicia con esas imágenes; no hemos probado
ninguna de las dos cosas en esta placa.

La imagen no lleva ninguna clave SSH: añade la tuya a
`/root/.ssh/authorized_keys` en el sistema instalado. La red por cable toma
dirección por DHCP, y SSH solo acepta claves.

## Qué hace distinto el perfil

- **Kernel y arranque.** El kernel y el árbol de dispositivos de Radxa,
  arrancados con entradas de arranque de tipo 1; un menú oculto con entradas
  que muestran el arranque por HDMI, para diagnóstico (lo muestra una tecla
  mantenida al encender). Su entrada «MUN OS diagnostic» arranca la placa
  sin la interfaz y abre una línea de órdenes de root en tty1, por HDMI y
  con un teclado USB, sin contraseña (`systemd.debug_shell=tty1`,
  [`mkosi.postoutput`](../../os/mkosi/mkosi.profiles/q6a-dev/mkosi.postoutput)).
  Quien pueda mantener una tecla al encender tiene root: la entrada existe
  para una placa de desarrollo y no debe llevarse a una imagen de consola.
- **Pantalla.** El shell dibuja con la GPU (eglfs de Qt sobre KMS, freedreno
  de Mesa) a 1920x1080 y 60 Hz prefiera lo que prefiera la pantalla, y el
  kernel arranca también en ese modo. El enlace se limita a 8 bits por canal
  (el puente de DisplayPort a HDMI de la placa lava la entrada de 10 bits),
  y el límite se pone antes de mostrar nada.
- **El arranque.** La consola muestra la imagen de espera de MUN desde que
  el sistema puede dibujar, y después su arranque con sonido. El firmware de
  la placa espera 1 s una tecla al encender en vez de sus 5: es un ajuste
  del firmware, guardado en su flash, que la imagen impone en cada arranque
  (`/usr/lib/mun/board-firmware-wait`); F2 y F12 al encender siguen llegando
  a sus menús, y enmascarar `mun-board-firmware-wait.service` conserva otra
  espera.
- **Sonido.** Por HDMI, a través del DSP de audio de la placa. La tarjeta
  de sonido aparece unos 10 s después de arrancar (el kernel del fabricante
  espera 5 s una respuesta del DSP), así que el shell carga mientras tanto
  y su arranque espera al sonido, hasta 30 s (`MUN_SHELL_SOUND_WAIT`). La
  tarjeta de sonido aparece un momento antes de que se ponga su ruta a HDMI, y un
  dispositivo abierto entre medias suena hacia nada y falla: dentro de esa
  espera el shell solo abre el dispositivo cuando existe el archivo que
  escribe el script de la ruta de la placa
  (`MUN_SHELL_SOUND_READY=/run/mun-board-audio/route-set`, puesto por el
  añadido del perfil a la unidad del shell). A un DSP cuyos servicios no
  llegan a Linux se le reinicia el enlace una vez. No se restaura ningún estado
  guardado del mezclador: en esta placa esa restauración apagaba la ruta a
  HDMI.
- **Game Cards.** Una imagen de tarjeta escrita entera en una tarjeta
  microSD es una Game Card en la ranura de la placa (`MUN_CARDD_SLOTS`,
  [servicio de tarjetas](../../services/mun-cardd/README.md), en inglés).
- **Red.** El enlace por cable se mantiene fuera de los estados de ahorro de
  PCIe que lo tiraban al sacar una tarjeta de la ranura.
- **Soporte.** Un volcado de diagnóstico en la partición de arranque en
  cada arranque y apagado, y una línea por arranque sobre cómo arrancó la
  consola (tiempos, sonido, arranque, unidades fallidas), que se lee por SSH
  con `/usr/lib/mun/board-report`.

El [`mkosi.conf`](../../os/mkosi/mkosi.profiles/q6a-dev/mkosi.conf) del
perfil dice por qué está cada cosa.

## Qué se ha comprobado

Por nosotros, en nuestra única placa, con una persona delante de la
pantalla. El `BUILD-INFO.json` de una construcción nombra su commit; el
candidato es aquí aquel del que se hace la versión de esta página.

| Qué | Estado |
| --- | --- |
| Llegada de una tarjeta, navegación, jugar a MUN Collect y volver, un guardado escrito en la tarjeta física, retirada segura y brusca | Comprobado el 13 de noviembre de 2026, con una construcción anterior de esta versión preliminar instalada entera en la eMMC |
| Reef detrás de los menús (el movimiento de MUN Shape), atenuación a los diez minutos, *Reducir movimiento* | Comprobado el mismo día: 33,6 minutos en marcha sin ningún reinicio del shell ni de los servicios, con la pantalla atenuada a los diez. En esa prueba no se registraron los tiempos de fotograma y no dice nada de una tasa de fotogramas: lo medido en esta placa, donde el movimiento no mantiene 60 fotogramas por segundo, está en [dónde está el movimiento](../shape-motion.md#where-it-stands), en inglés |
| Arranque: un solo cambio de imagen tras el del firmware, imagen de espera, sonido desde su primera nota | Comprobado el 14 de noviembre de 2026 en tres tipos de arranque (tras desenchufar, tras un corte de corriente, con el botón), con un televisor; con el shell y los archivos de placa del candidato puestos sobre la instalación anterior |
| La imagen del candidato instalada entera, y el recorrido completo sobre ella | Pendiente |
| Un arranque sin dispositivo de sonido | Pendiente en la placa; lo cubren las comprobaciones del propio shell |
| Pantallas | Un monitor 1080p, un monitor 3440x1440 (el shell a 1080p) y un televisor, todos por HDMI |
| Mandos | Un mando, un GameSir Nova Lite por Bluetooth, manejó los menús en esta placa con una imagen de laboratorio anterior, con el Bluetooth configurado a mano. El perfil no configura Bluetooth, y con el candidato no se ha probado ningún mando |

## Límites conocidos

- Los 5 s que el kernel del fabricante espera al DSP de audio forman parte
  de cada arranque.
- El reinicio del enlace de un DSP cuyos servicios no llegan se hizo a mano
  en el único arranque que lo necesitó; el automático aún no se ha
  encontrado con un arranque así.
- La placa no tiene pila de reloj: sin un servidor de hora, su reloj
  empieza en una hora antigua en cada arranque.
- El sonido es solo por HDMI. Sin Wi-Fi ni configuración de Bluetooth.
- Los cortes de corriente se han sobrevivido, no estudiado: nada de esto
  establece qué conserva una tarjeta o el disco del sistema después de uno.
- Las cifras medidas aquí no dicen nada de la placa de la consola.
