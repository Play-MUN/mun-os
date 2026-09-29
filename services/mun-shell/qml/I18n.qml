pragma Singleton
import QtQuick
import MUN.Shell

// The interface's two languages: English, the default, and Spanish, chosen
// in Settings and kept by ShellSettings. Every string is written in both at
// the place it is used, t("English", "Español"), so neither can be missing;
// bindings that call t() follow a change of language at once.
//
// The console's services word their messages in Spanish. In English the
// interface says what it knows by the message's code and shows the service's
// own words only when it has nothing better.
QtObject {
    readonly property bool spanish: ShellSettings.language === "es"

    function t(en, es) { return spanish ? es : en }

    readonly property var locale: Qt.locale(spanish ? "es_ES" : "en_GB")

    function number(value, decimals) { return Number(value).toLocaleString(locale, "f", decimals) }

    // "7.8 GiB" / "7,8 GiB".
    function bytes(value) {
        const units = ["B", "KiB", "MiB", "GiB", "TiB"]
        let unit = 0
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024
            ++unit
        }
        return number(value, value < 10 && unit > 0 ? 1 : 0) + " " + units[unit]
    }

    // 24-hour "09:41"; 12-hour "9:41 AM" / "9:41 a. m.".
    function clock(date, format) {
        const h = date.getHours(), m = String(date.getMinutes()).padStart(2, "0")
        if (format !== "12h")
            return String(h).padStart(2, "0") + ":" + m
        return ((h + 11) % 12 + 1) + ":" + m + (h < 12 ? t(" AM", " a. m.") : t(" PM", " p. m."))
    }

    function moonName(phase) {
        const names = spanish
            ? ["Luna nueva", "Luna creciente", "Cuarto creciente", "Gibosa creciente", "Luna llena", "Gibosa menguante", "Cuarto menguante", "Luna menguante"]
            : ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]
        return names[Math.floor((phase * 8 + 0.5) % 8)]
    }

    readonly property string notAvailable: t("Not available yet", "No disponible todavía")

    // Why the card service refused a card (services/mun-cardd, tools/mun-card).
    function cardError(error) {
        const english = {
            "manifest_missing": "The card has no manifest (mun.toml).",
            "manifest_ambiguous": "The card has both mun.toml and neptune.toml.",
            "manifest_syntax": "The card's manifest is not valid TOML.",
            "manifest_too_large": "The card's manifest is larger than allowed.",
            "manifest_field": "A field of the card's manifest is missing or not valid.",
            "schema_unsupported": "This console does not know the card's format.",
            "version_invalid": "The card's content version is not valid.",
            "id_invalid": "The card's identifier is not valid.",
            "kind_unsupported": "This kind of content is not supported.",
            "arch_unsupported": "The card's content is built for another processor.",
            "profile_unsupported": "This console does not provide what the game needs to run.",
            "path_unsafe": "The manifest names a path outside the card's content.",
            "path_missing": "A file the manifest names is not on the card.",
            "path_symlink": "The card uses a symbolic link where none is allowed.",
            "path_type": "A path the manifest names is of the wrong type.",
            "cover_invalid": "The card's cover is not a valid PNG.",
            "cover_too_large": "The card's cover is larger than allowed.",
            "saves_directory": "The card's save declaration is not valid.",
            "saves_max_bytes": "The card's save declaration is not valid.",
            "another_card_active": "Another Game Card is in use.",
            "mount_failed": "The card could not be opened read-only.",
            "image_has_errors": "The card's file system is marked with errors.",
            "image_needs_recovery": "The card's file system needs recovery.",
            "card_unavailable": "The card could not be read.",
            "source_unreadable": "The card could not be read.",
            "internal_error": "The card could not be read."
        }
        return serviceText(error, english, "The card could not be used.", "La tarjeta no se puede usar.")
    }

    // Why the launcher refused to start a game or to release the card.
    function launchError(error) {
        const english = {
            "in_use": "A game session is using the card.",
            "card_in_use": "A game session is using the card.",
            "busy": "A game is already running.",
            "reader_unavailable": "The card reader is not available.",
            "card_mismatch": "The card changed. Try again.",
            "not_runnable": "The card has no game this console can run.",
            "prepare_failed": "The game could not be prepared."
        }
        return serviceText(error, english, "The console could not do that.", "La consola no pudo hacerlo.")
    }

    // How a game session ended (services/mun-launchd).
    function sessionTitle(reason) {
        switch (reason) {
        case "exited": return t("Session ended", "Sesión terminada")
        case "card_removed": return t("The Game Card was removed", "Se retiró la Game Card")
        case "reader_lost": return t("The card reader was lost", "Se perdió el lector de tarjetas")
        case "crashed": return t("The game closed unexpectedly", "El juego se cerró de forma inesperada")
        case "failed": return t("The game ended with an error", "El juego terminó con error")
        case "start_failed": return t("The game could not start", "El juego no pudo arrancar")
        case "interrupted": return t("The session was interrupted", "La sesión se interrumpió")
        case "entry_not_executable": return t("The Game Card has no valid program", "La Game Card no contiene un ejecutable válido")
        case "prepare_failed":
        case "entry_not_file":
        case "entry_too_large": return t("The game could not be prepared", "No se pudo preparar el juego")
        default: return t("Session finished", "Sesión finalizada")
        }
    }

    function sessionText(result) {
        const title = result.title ? result.title + " · " : ""
        if (spanish)
            return title + (result.message || "")
        switch (result.reason) {
        case "exited": return title + (result.code ? "The game ended with code " + result.code + "." : "The game ended.")
        case "crashed": return title + "The game stopped" + (result.signal ? " (signal " + result.signal + ")." : ".")
        case "card_removed": return title + "The card left the slot during the session."
        case "reader_lost": return title + "The console lost contact with its card reader."
        case "interrupted": return title + "The launcher restarted during the session."
        default: return title + (result.message || "")
        }
    }

    function savesText(saves) {
        if (!saves)
            return ""
        if (spanish)
            return saves.message || ""
        if (!saves.ok)
            return "The latest progress was not saved on the Game Card."
        return saves.synced_at ? "Progress saved on the Game Card." : ""
    }

    function platformText(platform) {
        if (!platform || !platform.message)
            return ""
        return spanish ? platform.message : "The console did not close the session normally."
    }

    // The service's own words in Spanish; in English the known meaning of
    // its code, else its words (they are all it has), else a generic line.
    function serviceText(error, english, genericEnglish, genericSpanish) {
        const code = error && error.code ? String(error.code) : ""
        const message = error && error.message ? String(error.message) : ""
        if (spanish)
            return message || genericSpanish
        return english[code] || message || genericEnglish
    }
}
