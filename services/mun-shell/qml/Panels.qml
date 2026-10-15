import QtQuick
import MUN.Shell

// What each entry's panel says and offers, from the console's real state:
// the card service, the launcher, the settings and the system. An option the
// console cannot perform yet says so ("Not available yet") instead of
// pretending; a value it can only show is shown as it is.
//
// `shell` is Main, which runs the actions.
QtObject {
    id: panels
    required property var shell

    function t(en, es) { return I18n.t(en, es) }

    function head(label) { return { kind: "head", label: label } }
    function action(label, value, run, note) { return { kind: "action", label: label, value: value || "", run: run, note: note || "" } }
    function info(label, value, note) { return { kind: "info", label: label, value: value || "", note: note || "" } }
    function choice(label, values, index, choose, note) {
        return { kind: "choice", label: label, values: values, index: Math.max(0, index), value: values[Math.max(0, index)],
                 choose: choose, note: note || "" }
    }
    function unavailable(label) { return info(label, I18n.notAvailable) }

    function build(key) {
        switch (key) {
        case "card": return card()
        case "games": return games()
        case "settings": return settings()
        case "power": return power()
        case "display": return display()
        case "sound": return sound()
        case "appearance": return appearance()
        case "language": return languageAndTime()
        case "network": return network()
        case "system": return system()
        }
        return { kicker: "", title: "", text: "", options: [] }
    }

    function waitingText() {
        const waiting = CardClient.waitingCount
        if (waiting === 0)
            return ""
        return " " + (waiting === 1 ? t("Another Game Card is waiting.", "Otra Game Card espera su turno.")
                                    : t(waiting + " more Game Cards are waiting.", "Otras " + waiting + " Game Cards esperan su turno."))
    }

    // What the card does to the console, as it is (src/shape.h).
    function shapeText() {
        switch (Shape.source) {
        case "shape": return " " + t("The game dresses the console while its card is in.",
                                     "El juego viste la consola mientras su tarjeta está dentro.")
        case "lent": case "read": return " " + t("The console takes the card's colours.", "La consola toma los colores de la tarjeta.")
        }
        return ""
    }

    function card() {
        const kicker = "GAME CARD"
        switch (shell.cardMode) {
        case "valid": {
            const info = CardClient.info
            const options = []
            if (info.kind === "game")
                options.push(action(t("Play", "Jugar"), shell.launchable ? "" : shell.launchBlocker, shell.play))
            options.push(action(t("Eject safely", "Retirar con seguridad"),
                                LaunchClient.releasePending ? t("Ejecting…", "Retirando…") : "", shell.eject))
            return {
                kicker: kicker,
                title: info.title || "Game Card",
                text: (info.kind === "game" ? t("The card is ready.", "La tarjeta está lista.") + shapeText()
                       : t("This Game Card holds no game.", "Esta Game Card no contiene ningún juego.")) + waitingText(),
                options: options
            }
        }
        case "invalid":
            return {
                kicker: kicker,
                title: t("Game Card not valid", "Game Card no válida"),
                text: I18n.cardError(CardClient.error) + waitingText(),
                options: [action(t("Details", "Detalles"), "", shell.showCardError)]
            }
        case "reading":
            return { kicker: kicker, title: t("Reading the Game Card…", "Leyendo la Game Card…"), text: t("One moment.", "Un momento."), options: [] }
        case "released":
            return {
                kicker: kicker,
                title: t("You can remove the Game Card", "Puedes retirar la Game Card"),
                text: t("The console is done with it. The game and your saves travel inside it.",
                        "La consola ha terminado con ella. El juego y tus partidas viajan dentro."),
                options: []
            }
        case "offline":
            return {
                kicker: kicker,
                title: t("Card reader unavailable", "Lector de tarjetas no disponible"),
                text: t("The console cannot reach its card reader. It reconnects on its own.",
                        "La consola no encuentra su lector de tarjetas. Se reconecta sola."),
                options: []
            }
        }
        return {
            kicker: kicker,
            title: t("Slot empty", "Ranura vacía"),
            text: t("Insert a Game Card into the side of the console. The game and your saves travel inside it.",
                    "Inserta una Game Card por el lateral de la consola. El juego y tus partidas viajan dentro de ella.") + waitingText(),
            options: []
        }
    }

    function games() {
        const title = CardClient.info.title || "Game Card"
        return {
            kicker: t("MY GAMES", "MIS JUEGOS"),
            title: t("My games", "Mis juegos"),
            text: shell.hasGame ? t("Right now you have " + title + " at hand.", "Ahora mismo tienes a mano " + title + ".")
                                : t("There are no games on the console. Your games live on their Game Cards.",
                                    "No hay ningún juego en la consola. Tus juegos viven en sus Game Cards."),
            options: shell.hasGame ? [action(title, shell.launchable ? t("Play", "Jugar") : shell.launchBlocker, shell.play)] : []
        }
    }

    function settings() {
        return {
            kicker: t("SETTINGS", "CONFIGURACIÓN"),
            title: t("Settings", "Configuración"),
            text: t("Display, sound, appearance, language and time, network and system.",
                    "Pantalla, sonido, apariencia, idioma y hora, red y sistema."),
            options: []
        }
    }

    function power() {
        return {
            kicker: t("POWER", "ENERGÍA"),
            title: t("Turn off", "Apagar"),
            text: t("Turns the console off cleanly after you confirm. Your games and saves stay on their Game Cards.",
                    "Apaga la consola de forma segura cuando lo confirmes. Tus juegos y partidas siguen en sus Game Cards."),
            options: []
        }
    }

    // The six sections. Each option says what it is today: one that works
    // changes the console at once (the resolution through its dialog); one
    // that only informs shows the value as the system has it; one the console
    // does not have yet says "Not available yet". Nothing here needs an
    // account.
    function display() {
        const areas = [100, 97, 94, 91]
        return {
            kicker: t("DISPLAY", "PANTALLA"),
            title: t("Display", "Pantalla"),
            text: t("What reaches your TV.", "Lo que llega a tu televisor."),
            options: [
                // Chosen in a dialog, since a change restarts the shell; only
                // shown where the display offers no choice.
                ShellSettings.resolutions.length > 0
                    ? action(t("Resolution", "Resolución"), resolutionValue(), shell.askResolution,
                             t("If the picture does not come back, the console returns to the previous one on its own.",
                               "Si la imagen no vuelve, la consola recupera la anterior sola."))
                    : info(t("Resolution", "Resolución"), resolutionValue()),
                choice(t("Safe area", "Zona segura"), areas.map(a => a + " %"), areas.indexOf(ShellSettings.safeArea),
                       i => ShellSettings.safeArea = areas[i],
                       t("Brings the interface in from the edges of a TV that crops them.",
                         "Aleja la interfaz de los bordes en un televisor que los recorta.")),
                unavailable("HDR"),
                unavailable(t("Refresh rate", "Frecuencia"))
            ]
        }
    }

    function sound() {
        return {
            kicker: t("SOUND", "SONIDO"),
            title: t("Sound", "Sonido"),
            text: t("What reaches your ears.", "Lo que llega a tus oídos."),
            options: [
                choice(t("System sounds", "Sonidos del sistema"), [t("On", "Activados"), t("Off", "Desactivados")],
                       ShellSettings.systemSounds ? 0 : 1, i => ShellSettings.systemSounds = i === 0,
                       t("The menus' sounds and the start-up's.", "Los sonidos de los menús y el del arranque.")),
                choice(t("Game sounds on the menus", "Sonidos del juego en los menús"), [t("On", "Activados"), t("Off", "Desactivados")],
                       ShellSettings.gameSounds ? 0 : 1, i => ShellSettings.gameSounds = i === 0,
                       t("A Game Card's own sounds for the menus while it is in.",
                         "Los sonidos propios de una Game Card en los menús mientras está dentro.")),
                // The start-up's sound plays with the system sounds.
                info(t("Startup sound", "Sonido de arranque"), ShellSettings.systemSounds ? t("On", "Activado") : t("Off", "Desactivado"),
                     t("Follows System sounds.", "Sigue a Sonidos del sistema.")),
                unavailable(t("Output", "Salida")),
                unavailable(t("Format", "Formato"))
            ]
        }
    }

    function appearance() {
        return {
            kicker: t("APPEARANCE AND ACCESSIBILITY", "APARIENCIA Y ACCESIBILIDAD"),
            title: t("Appearance and accessibility", "Apariencia y accesibilidad"),
            text: t("How the console looks and moves.", "Cómo se ve y se mueve la consola."),
            options: [
                // How a Game Card dresses the console (docs/shape.md).
                choice("MUN Shape", [t("Full", "Completo"), t("Colours only", "Solo colores"), t("Off", "Desactivado")],
                       ["full", "colours", "off"].indexOf(ShellSettings.shapeMode),
                       i => ShellSettings.shapeMode = ["full", "colours", "off"][i],
                       t("How a Game Card dresses the console while it is in.",
                         "Cómo viste una Game Card la consola mientras está dentro.")),
                // Home's world and objects hold still; the start-up keeps its
                // times without its shake, tremor and flicker.
                choice(t("Reduce motion", "Reducir movimiento"), [t("Off", "Desactivado"), t("On", "Activado")],
                       ShellSettings.reduceMotion ? 1 : 0, i => ShellSettings.reduceMotion = i === 1,
                       t("Home and its worlds hold still; the start-up loses its shake and flicker.",
                         "Home y sus mundos se quedan quietos; el arranque pierde su temblor y sus destellos.")),
                unavailable(t("Status light", "Luz de estado"))
            ]
        }
    }

    function languageAndTime() {
        return {
            kicker: t("LANGUAGE AND TIME", "IDIOMA Y HORA"),
            title: t("Language and time", "Idioma y hora"),
            text: t("The console's language and its clock.", "El idioma de la consola y su reloj."),
            options: [
                choice(t("Language", "Idioma"), ["English", "Español"], I18n.spanish ? 1 : 0,
                       i => ShellSettings.language = i === 1 ? "es" : "en"),
                choice(t("Time format", "Formato de hora"), [t("24-hour", "24 horas"), t("12-hour", "12 horas")],
                       ShellSettings.clockFormat === "12h" ? 1 : 0, i => ShellSettings.clockFormat = i === 1 ? "12h" : "24h"),
                info(t("Time zone", "Zona horaria"), SystemInfo.timeZone),
                unavailable(t("Set time", "Ajuste de hora"))
            ]
        }
    }

    // "2560x1440" → "1440p", "2560 × 1440".
    function resolutionName(mode) { return mode.split("x")[1] + "p" }
    function resolutionSize(mode) { return mode.replace("x", " × ") }
    function resolutionValue() {
        const active = ShellSettings.activeResolution
        if (active !== "auto")
            return resolutionName(active)
        return SystemInfo.displayHeight > 0 ? t("Automatic (" + SystemInfo.displayHeight + "p)", "Automática (" + SystemInfo.displayHeight + "p)")
                                            : t("Automatic", "Automática")
    }

    function network() {
        const wired = SystemInfo.wiredConnected ? t("Connected", "Conectado")
                    : SystemInfo.wiredPresent ? t("Not connected", "Sin conectar") : t("Not detected", "No detectado")
        const wifi = !SystemInfo.wifiPresent ? t("No adapter", "Sin adaptador")
                   : SystemInfo.wifiConnected ? t("Connected", "Conectado") : I18n.notAvailable
        return {
            kicker: t("NETWORK", "RED"),
            title: t("Network", "Red"),
            text: t("MUN needs no connection to play. It only uses one if you want it to.",
                    "MUN no necesita conexión para jugar. Solo la usa si tú quieres."),
            options: [
                head(t("WIRED", "CABLE")),
                info(t("Ethernet", "Cable"), wired,
                     SystemInfo.addresses.length > 0 ? SystemInfo.addresses.join(" · ")
                     : SystemInfo.wiredPresent ? t("Plug in a network cable.", "Conecta un cable de red.")
                     : t("The console has no wired network interface.", "La consola no tiene interfaz de red por cable.")),
                head("WI-FI"),
                info("Wi-Fi", wifi,
                     SystemInfo.wifiPresent ? t("Joining networks is not available yet.", "Conectarse a redes no está disponible todavía.")
                     : t("The console has no Wi-Fi adapter.", "La consola no tiene adaptador Wi-Fi."))
            ]
        }
    }

    function system() {
        const hours = [0, 1, 3, 6]
        return {
            kicker: t("SYSTEM", "SISTEMA"),
            title: t("System", "Sistema"),
            text: t("Power and the console's software.", "Energía y software de la consola."),
            options: [
                head(t("POWER", "ENERGÍA")),
                choice(t("Auto power off", "Apagado automático"),
                       [t("Never", "Nunca"), t("After 1 hour", "Tras 1 hora"), t("After 3 hours", "Tras 3 horas"), t("After 6 hours", "Tras 6 horas")],
                       hours.indexOf(ShellSettings.autoPowerOffHours), i => ShellSettings.autoPowerOffHours = hours[i]),
                action(t("Turn off console", "Apagar consola"), "", shell.askPowerOff),
                head("SOFTWARE"),
                unavailable(t("Updates", "Actualizaciones")),
                // Set by the image: on for a development build.
                info(t("Developer mode", "Modo desarrollador"), SystemInfo.release ? t("Off", "Desactivado") : t("On", "Activado")),
                action(t("About", "Información"), "", shell.showAbout),
                action(t("Reset settings", "Restablecer ajustes"), "", shell.askReset)
            ]
        }
    }

    // ---------------------------------------------------------------- dialogs

    function about() {
        SystemInfo.refresh()
        const stage = !SystemInfo.fromImage ? t("lab installation", "instalación de laboratorio")
                    : (SystemInfo.release ? t("release", "versión publicada") : t("development", "desarrollo"))
                      + (SystemInfo.environment ? " · " + SystemInfo.environment : "")
        const facts = [
            t("Machine: ", "Máquina: ") + (SystemInfo.machine || "—"),
            t("Processor: ", "Procesador: ") + SystemInfo.cpuCores + t(" cores · ", " núcleos · ") + SystemInfo.cpuName,
            t("Memory: ", "Memoria: ") + I18n.bytes(SystemInfo.memoryAvailable) + t(" free of ", " libres de ") + I18n.bytes(SystemInfo.memoryTotal),
            t("System storage: ", "Almacenamiento del sistema: ") + I18n.bytes(SystemInfo.storageFree) + t(" free of ", " libres de ") + I18n.bytes(SystemInfo.storageTotal),
            t("Display: ", "Pantalla: ") + SystemInfo.displayWidth + " × " + SystemInfo.displayHeight + " · " + SystemInfo.displayPath,
            t("Network: ", "Red: ") + (SystemInfo.addresses.length > 0 ? SystemInfo.addresses.join(" · ") : t("offline", "sin conexión")),
            t("Kernel: ", "Núcleo: ") + SystemInfo.kernel,
            t("Build: ", "Compilación: ") + SystemInfo.buildId + " · MUN Shell " + SystemInfo.shellVersion
        ]
        return {
            title: t("About this console", "Información del sistema"),
            text: SystemInfo.osTitle + " · " + stage + ". " + t("Saves live on each Game Card.", "Las partidas se guardan en cada Game Card."),
            facts: facts,
            actions: [{ label: t("Close", "Cerrar"), run: shell.closeModal }]
        }
    }

    function powerOff() {
        return {
            title: t("Turn off the console?", "¿Apagar la consola?"),
            text: t("If there is a Game Card inside, your save is already on it.", "Si hay una Game Card, tu partida ya está guardada en ella."),
            actions: [{ label: t("Turn off", "Apagar"), run: shell.powerOff }, { label: t("Cancel", "Cancelar"), run: shell.closeModal }]
        }
    }

    function powerOffFailed(detail) {
        return {
            title: t("The console could not turn off", "La consola no pudo apagarse"),
            text: t("It is still on. Try again in a moment.", "Sigue encendida. Vuelve a intentarlo en un momento."),
            facts: detail ? [detail] : [],
            actions: [{ label: t("Close", "Cerrar"), run: shell.closeModal }]
        }
    }

    // The display's own mode first, then each mode it offers; the focus
    // starts on the one in use.
    function resolution() {
        const active = ShellSettings.activeResolution
        const own = ShellSettings.nativeResolution
        const inUse = t("In use", "En uso")
        const actions = [{ label: t("Automatic", "Automática"), value: active === "auto" ? inUse : own ? resolutionName(own) : "",
                           run: () => shell.changeResolution("auto") }]
        const modes = ShellSettings.resolutions
        for (let i = 0; i < modes.length; ++i) {
            const mode = modes[i]
            actions.push({ label: resolutionName(mode), value: mode === active ? inUse : resolutionSize(mode), run: () => shell.changeResolution(mode) })
        }
        actions.push({ label: t("Cancel", "Cancelar"), run: shell.closeModal })
        return {
            title: t("Resolution", "Resolución"),
            text: t("The screen goes dark for a moment. If the picture does not come back, the console returns to the previous resolution after "
                    + shell.trialLength + " seconds.",
                    "La pantalla se apaga un momento. Si la imagen no vuelve, la consola recupera la resolución anterior a los "
                    + shell.trialLength + " segundos."),
            actions: actions,
            initial: Math.max(0, active === "auto" ? 0 : modes.indexOf(active) + 1)
        }
    }

    // After a change of resolution: kept only if the player says so.
    function resolutionTrial() {
        return {
            title: t("Keep this resolution?", "¿Mantener esta resolución?"),
            text: () => t("Without an answer the console returns to the previous one in " + shell.trialSeconds + " s.",
                          "Sin respuesta, la consola vuelve a la anterior en " + shell.trialSeconds + " s."),
            actions: [{ label: t("Keep", "Mantener"), run: shell.keepResolution },
                      { label: t("Go back", "Volver a la anterior"), run: shell.revertResolution }],
            dismiss: shell.revertResolution,
            trial: true
        }
    }

    function reset() {
        return {
            title: t("Reset settings?", "¿Restablecer los ajustes?"),
            text: t("Every setting returns to its default, except the language. Your games and saves are untouched: they live on their Game Cards.",
                    "Todos los ajustes vuelven a su valor inicial, salvo el idioma. Tus juegos y partidas no se tocan: viven en sus Game Cards.")
                  + (ShellSettings.activeResolution !== "auto"
                     ? t(" The screen goes dark for a moment while the resolution returns to automatic.",
                         " La pantalla se apaga un momento mientras la resolución vuelve a automática.") : ""),
            actions: [
                { label: t("Reset", "Restablecer"), run: () => { ShellSettings.resetKeepingLanguage(); shell.closeModal() } },
                { label: t("Cancel", "Cancelar"), run: shell.closeModal }
            ]
        }
    }

    function cardError() {
        const error = CardClient.error
        const facts = [t("Code: ", "Código: ") + (error.code || "—")]
        if (error.detail)
            facts.push(t("Detail: ", "Detalle: ") + error.detail)
        facts.push(t("The card was not opened and will not be used. Remove it and check it with mun-card inspect.",
                     "La tarjeta no se ha abierto ni se usará. Retírala y compruébala con mun-card inspect."))
        return {
            title: t("Game Card not valid", "Game Card no válida"),
            text: I18n.cardError(error),
            facts: facts,
            actions: [{ label: t("Close", "Cerrar"), run: shell.closeModal }]
        }
    }

    function sessionResult(result, key) {
        const facts = [I18n.savesText(result.saves), I18n.platformText(result.platform)]
        if (result.reason !== "exited" && result.detail)
            facts.push(String(result.detail))
        return {
            title: I18n.sessionTitle(result.reason),
            text: I18n.sessionText(result),
            facts: facts.filter(f => f !== ""),
            actions: [{ label: t("OK", "Aceptar"), run: shell.dismissResult }],
            dismiss: shell.dismissResult,
            resultKey: key
        }
    }
}
