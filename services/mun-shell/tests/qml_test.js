// Host-side checks of the logic the interface keeps in its QML files: the
// functions are read out of the files as they are written and run by node
// against stand-ins for what they use (the sound player, the timers, the
// settings), on a clock the checks move. A name a stand-in lacks is an error,
// never a global. No Qt, no display, no device. Run by `make check` where
// node is installed (tests/Makefile here).
const fs = require("fs");
const path = require("path");

let passed = 0;
let failed = 0;

function check(what, ok, detail) {
    if (ok) {
        ++passed;
    } else {
        ++failed;
        console.log("FAIL " + what + (detail === undefined ? "" : ": " + detail));
    }
}

function qml(name) {
    return fs.readFileSync(path.join(__dirname, "..", "qml", name), "utf8");
}

// The text of `function name(...) { ... }` declared at `indent` spaces, the
// first one after `from`.
function functionText(source, name, indent, from = 0) {
    const pad = " ".repeat(indent);
    const at = source.indexOf("\n" + pad + "function " + name + "(", from);
    const end = source.indexOf("\n" + pad + "}\n", at);
    if (at < 0 || end < 0)
        throw new Error("no function " + name + " at " + indent + " spaces");
    // Written on one line, or over several down to its closing brace.
    const line = source.slice(at + 1, source.indexOf("\n", at + 1));
    if (line.endsWith("}"))
        return line;
    return source.slice(at + 1, end + pad.length + 2);
}

// The body of a handler written `name: { ... }` at `indent` spaces, the
// first one after `from`, as a function.
function handlerText(source, name, indent, from = 0) {
    const pad = " ".repeat(indent);
    const at = source.indexOf("\n" + pad + name + ": {\n", from);
    const end = source.indexOf("\n" + pad + "}\n", at);
    if (at < 0 || end < 0)
        throw new Error("no handler " + name + " at " + indent + " spaces");
    return "function () " + source.slice(at + 1 + pad.length + name.length + 2, end + pad.length + 2);
}

// QML resolves a bare name on the object the code belongs to; here, on
// `scope`, and on nothing else but JavaScript's own globals.
function bind(scope, text) {
    const names = new Proxy(scope, {
        has: (target, key) => typeof key === "string",
        get: (target, key) => {
            if (key in target)
                return target[key];
            if (typeof key === "symbol")
                return undefined;
            if (key in globalThis)
                return globalThis[key];
            throw new ReferenceError("the stand-in has no " + key);
        },
        set: (target, key, value) => {
            if (!(key in target))
                throw new ReferenceError("the stand-in has no " + key);
            target[key] = value;
            return true;
        }
    });
    return new Function("names", "with (names) { return (" + text + ") }")(names);
}

function constant(source, name) {
    const found = new RegExp("property (?:int|double|real) " + name + ": (-?[0-9.]+)").exec(source);
    if (!found)
        throw new Error("no property " + name);
    return Number(found[1]);
}

// ---------------------------------------------------------------- the start-up

// A sound device as the start-up asks about it (src/systemsounds.h), in
// seconds since the shell started: until when one is expected, when it first
// opened, when it closed again, and since when it has played well.
function soundDevice(spec, time) {
    const { expectedUntil = 0, opensAt = Infinity, closesAt = Infinity, playsWellFrom = Infinity } = spec;
    return {
        playingFor: () => time() >= playsWellFrom && time() < closesAt ? time() - playsWellFrom : -1,
        sinceFirstOpen: () => time() >= opensAt ? time() - opensAt : -1,
        expecting: () => time() < expectedUntil,
        deviceOpen: () => time() >= opensAt && time() < closesAt,
        recoveries: () => 0
    };
}

// BootLayer.qml's start-up from start() until it begins: when it began (in
// seconds since the shell started), whether with its sound, and what it told
// the journal. `loadedAt`: when the interface has loaded and calls start().
function startUp(spec, { loadedAt = 1.0, systemSounds = true } = {}) {
    const source = qml("BootLayer.qml");
    let ms = Math.round(loadedAt * 1000);
    const time = () => ms / 1000;
    const result = { begunAt: null, sound: false, journal: [] };
    const layer = {
        t: -1, shown: true, running: false, waiting: false, warming: false, before: true, leaving: false, late: false,
        kWarm: constant(source, "kWarm"), kWarmLimit: constant(source, "kWarmLimit"),
        warmFrom: constant(source, "warmFrom"),
        ready_: { restart() {}, stop() {} },
        warm: { running: false, start() { this.running = true; }, stop() { this.running = false; } },
        clock: { begin() { result.begunAt = time(); } },
        ShellSettings: { systemSounds: systemSounds },
        SystemSounds: Object.assign(soundDevice(spec, time), { play(name) { result.sound = name === "startup"; } }),
        // The wall clock, as far from zero as a real one.
        Date: { now: () => 1794700000000 + ms },
        console: { info: (line) => result.journal.push(line) }
    };
    layer.root = layer;
    for (const name of ["start", "soundReady", "begin"])
        layer[name] = bind(layer, functionText(source, name, 4));
    const warmTriggered = bind(layer, handlerText(source, "onTriggered", 8, source.indexOf("id: warm\n")));
    const interval = Number(/id: warm\n\s+interval: (\d+)/.exec(source)[1]);

    layer.start();
    while (layer.warm.running && time() < 120) {
        ms += interval;
        warmTriggered();
    }
    result.warming = layer.warming;
    return result;
}

function near(value, wanted) {
    // The start-up looks at the device every 50 ms.
    return value !== null && value >= wanted - 1e-9 && value <= wanted + 0.05 + 1e-9;
}

function startUpChecks() {
    // The device opens as the shell starts and plays well from 0.6 s; the
    // interface has loaded at 1 s. Not expected (no MUN_SHELL_SOUND_WAIT).
    let r = startUp({ opensAt: 0.2, playsWellFrom: 0.6 });
    check("a device that has played well for 0.4 s: the start-up waits until it has for 2 s", near(r.begunAt, 2.6) && r.sound,
          "began at " + r.begunAt + " s");
    check("and the journal says how long it waited", r.journal.length === 1 && /waited 1\.6\d s for the sound device: it had played well/.test(r.journal[0]),
          JSON.stringify(r.journal));
    r = startUp({ opensAt: 0.2, playsWellFrom: 0.6 }, { loadedAt: 3.0 });
    check("a device that has played well for 2 s already: the start-up begins at once", r.begunAt === 3.0 && r.sound && r.journal.length === 0,
          "began at " + r.begunAt + " s");

    // A board's card, which comes after the shell (MUN_SHELL_SOUND_WAIT=30).
    r = startUp({ expectedUntil: 30, opensAt: 6.0, playsWellFrom: 6.4 });
    check("an expected device: the start-up waits for it to come and play well for 2 s", near(r.begunAt, 8.4) && r.sound,
          "began at " + r.begunAt + " s");
    r = startUp({ expectedUntil: 30 });
    check("an expected device that never comes: the start-up begins when the wait is over", near(r.begunAt, 30) && r.sound,
          "began at " + r.begunAt + " s");
    check("and the journal says none came", r.journal.length === 1 && /none came$/.test(r.journal[0]), JSON.stringify(r.journal));
    r = startUp({ expectedUntil: 30, opensAt: 6.0 });
    check("an expected device that comes and never plays well: 5 s from its first opening", near(r.begunAt, 11.0) && r.sound,
          "began at " + r.begunAt + " s");

    // No device, and none expected: nothing to wait for.
    r = startUp({});
    check("no device and none expected: the start-up begins at once", r.begunAt === 1.0 && r.sound && r.journal.length === 0,
          "began at " + r.begunAt + " s");
    r = startUp({ opensAt: 0.2, closesAt: 0.5 });
    check("a device that failed and is closed: the start-up begins at once", r.begunAt === 1.0 && r.sound,
          "began at " + r.begunAt + " s");

    // Open, and it never plays well: the start-up still begins.
    r = startUp({ opensAt: 0.2 });
    check("a device that is open and never plays well: the start-up waits for it 3 s, no more", near(r.begunAt, 4.0) && r.sound,
          "began at " + r.begunAt + " s");
    check("and the journal says it did not play well", r.journal.length === 1 && /it came but did not play well$/.test(r.journal[0]),
          JSON.stringify(r.journal));
    r = startUp({ opensAt: 0.2 }, { loadedAt: 4.5 });
    check("the same, the interface loaded late: 5 s from its first opening", near(r.begunAt, 5.2) && r.sound,
          "began at " + r.begunAt + " s");

    r = startUp({ opensAt: 0.2, playsWellFrom: 0.6 }, { systemSounds: false });
    check("System sounds off: the start-up begins at once, without its sound", r.begunAt === 1.0 && !r.sound && !r.warming,
          "began at " + r.begunAt + " s");
}

// ---------------------------------------------------------------- awake

// Main.qml keeps the sound device open while the interface is in use: a
// binding on whether Home is powered and whether the start-up's layer is
// shown. Here Main.qml's and BootLayer.qml's own functions hand the screen
// over to each other, and `seen` holds every value the binding takes as they
// write those two, one write at a time, as QML evaluates it.
function handOver({ powered, shown, systemSounds = true }) {
    const main = qml("Main.qml");
    const layer = qml("BootLayer.qml");
    const state = { powered: powered, shown: shown };
    const seen = [];
    const calls = [];
    const note = (name) => () => { calls.push(name); };
    const window = {
        modal: "a dialog",
        ShellSettings: { systemSounds: systemSounds },
        PowerControl: { lastError: "it failed", busy: false, lastAction: "off", lastStatus: 0,
                        powerOff: note("PowerControl.powerOff"), resetConsole: note("PowerControl.resetConsole") },
        shutdownSound: { start: note("shutdownSound.start") },
        panels: { powerOffFailed: () => "failed", resetConsoleRefused: () => "refused" },
        sound: (name) => { calls.push("sound " + name); },
        openModal: note("openModal"),
        afterStart: note("afterStart")
    };
    const boot = {
        t: 3, running: true, waiting: false, warming: false, before: false, leaving: false, canSkip: true,
        warm: { stop() {} },
        ready_: { stop() {} },
        SystemSounds: { stop() {} }
    };
    const awake = bind(window, "function () { return " + /property: "awake"\n\s+value: ([^\n]+)\n/.exec(main)[1] + " }");
    const watched = (object, name) => Object.defineProperty(object, name, {
        enumerable: true,
        get: () => state[name],
        set: (value) => {
            if (value === state[name])
                return;
            state[name] = value;
            seen.push(awake());
        }
    });
    watched(window, "powered");
    watched(boot, "shown");
    window.window = window;
    window.boot = boot;
    boot.root = boot;
    for (const name of ["powerOff", "resetConsole"])
        window[name] = bind(window, functionText(main, name, 4));
    window.powerFailed = bind(window, functionText(main, "onLastErrorChanged", 8, main.indexOf("target: PowerControl\n")));
    for (const name of ["leave", "skip", "powerOff"])
        boot[name] = bind(boot, functionText(layer, name, 4));
    // The layer's signal, received by Main.qml's handler on it.
    boot.finished = bind(window, handlerText(main, "onFinished", 12, main.indexOf("id: boot\n")));
    return { window: window, boot: boot, seen: seen, calls: calls, awake: awake };
}

function awakeChecks() {
    const held = (seen) => seen.length > 0 && seen.every((value) => value === true);

    let h = handOver({ powered: true, shown: false });
    h.window.powerOff();
    check("turning off: the sound device is kept open across the hand-over to the dark screen", held(h.seen), JSON.stringify(h.seen));
    check("and the console turns off as before: the screen dark, no input, its sound asked for and waited for",
          h.boot.shown && !h.window.powered && h.window.modal === null && h.boot.t === -1
          && h.calls.join() === "sound shutdown,shutdownSound.start", h.calls.join());

    h = handOver({ powered: true, shown: false });
    h.window.resetConsole();
    check("resetting: the same hand-over, the device kept open", held(h.seen), JSON.stringify(h.seen));
    check("and the console resets as before", h.boot.shown && !h.window.powered && h.calls.join() === "PowerControl.resetConsole",
          h.calls.join());

    h = handOver({ powered: false, shown: true });
    h.boot.leave();
    check("the start-up ending: the device is kept open across the hand-over to Home", held(h.seen), JSON.stringify(h.seen));
    check("and Home comes in as before: powered, the layer gone, what follows the start run once",
          h.window.powered && !h.boot.shown && h.boot.leaving && !h.boot.running && h.calls.join() === "afterStart", h.calls.join());

    h = handOver({ powered: false, shown: true });
    h.boot.skip();
    check("the start-up skipped: the same", held(h.seen) && h.window.powered && !h.boot.shown, JSON.stringify(h.seen));

    h = handOver({ powered: false, shown: true });
    h.window.powerFailed();
    check("turning off failed: the device is kept open as the screen comes back", held(h.seen), JSON.stringify(h.seen));
    check("and the screen comes back as before, with its dialog", h.window.powered && !h.boot.shown
          && h.calls.join() === "sound error,openModal", h.calls.join());

    // The binding still lets the device go when it should.
    h = handOver({ powered: true, shown: false, systemSounds: false });
    h.window.powerOff();
    check("System sounds off: the device is not held, and the console turns off at once",
          h.seen.every((value) => value === false) && h.calls.join() === "PowerControl.powerOff", h.calls.join());
    h = handOver({ powered: false, shown: false });
    check("neither Home nor the start-up's layer: not awake", h.awake() === false);
}

// ---------------------------------------------------------------- what the panels say

// I18n.qml's own functions, in one of the interface's two languages.
function i18n(spanish) {
    const source = qml("I18n.qml");
    const object = { spanish: spanish, notAvailable: spanish ? "No disponible todavía" : "Not available yet" };
    for (const name of ["t", "serviceText", "launchError", "savesText"])
        object[name] = bind(object, functionText(source, name, 4));
    return object;
}

// Panels.qml's own card() and games() for a valid Game Card with a game,
// against a launcher that answers (idle or busy) or does not.
function panels({ spanish = false, launcher = "idle", orbital = true }) {
    const source = qml("Panels.qml");
    const main = qml("Main.qml");
    const LaunchClient = { available: launcher !== "down", state: launcher === "busy" ? "running" : "idle", releasePending: false };
    const I18n = i18n(spanish);
    const shell = { orbital: orbital, cardMode: "valid", hasGame: true, play() {}, eject() {}, LaunchClient: LaunchClient, I18n: I18n };
    shell.launchable = LaunchClient.available && LaunchClient.state === "idle";
    // What Main.qml says in Play's place when it cannot be pressed.
    shell.launchBlocker = bind(shell, "function () { return " + /property string launchBlocker: ([^\n]+)\n/.exec(main)[1] + " }")();
    const object = {
        shell: shell, I18n: I18n, LaunchClient: LaunchClient,
        CardClient: { info: { kind: "game", title: "MUN Collect" }, waitingCount: 0 },
        Shape: { source: "" }
    };
    for (const name of ["t", "action", "waitingText", "shapeText", "needsLauncher", "card", "games"]) {
        if (source.indexOf("\n    function " + name + "(") >= 0)
            object[name] = bind(object, functionText(source, name, 4));
    }
    return object;
}

function panelChecks() {
    for (const spanish of [false, true]) {
        const language = spanish ? "Spanish" : "English";
        const restart = spanish ? /apágala y vuelve a encenderla/ : /turn the console off and on again/;
        const off = spanish ? /apaga la consola antes de sacar la tarjeta/ : /turn the console off before taking the card out/;

        let options = panels({ spanish: spanish, launcher: "down" }).card().options;
        const [play, eject] = options;
        check(language + ": the launcher does not answer: Play says it is unavailable, and why and what to do",
              play.value === (spanish ? "No disponible" : "Unavailable") && restart.test(play.note), JSON.stringify(play));
        check(language + ": and Eject safely, which would do nothing, says so too",
              eject.value === (spanish ? "No disponible" : "Unavailable") && off.test(eject.note), JSON.stringify(eject));
        for (const orbital of [true, false]) {
            options = panels({ spanish: spanish, launcher: "down", orbital: orbital }).games().options;
            check(language + ": the same under " + (orbital ? "Universe" : "My games"),
                  options.length === 1 && options[0].value === (spanish ? "No disponible" : "Unavailable") && restart.test(options[0].note),
                  JSON.stringify(options));
        }

        options = panels({ spanish: spanish }).card().options;
        check(language + ": the launcher idle: Play and Eject safely say nothing more",
              options.length === 2 && options.every((option) => option.value === "" && option.note === ""), JSON.stringify(options));
        options = panels({ spanish: spanish, launcher: "busy" }).card().options;
        check(language + ": the launcher busy: Play says so, with no advice to restart",
              options[0].value === (spanish ? "Ocupada" : "Busy") && options[0].note === "" && options[1].note === "",
              JSON.stringify(options));
    }

    // The shell's own refusal when the launcher is lost during a safe
    // release (src/launchclient.cpp): its words in Spanish, its meaning in
    // English, never Spanish words in the English interface.
    const lost = /emit releaseFinished\(false, QStringLiteral\("([a-z_]+)"\), QStringLiteral\("([^"]+)"\)\)/
        .exec(fs.readFileSync(path.join(__dirname, "..", "src", "launchclient.cpp"), "utf8"));
    const error = { code: lost[1], message: lost[2] };
    check("English: the launcher lost during a safe release is said in English",
          /^The console lost contact with its launcher\./.test(i18n(false).launchError(error)), i18n(false).launchError(error));
    check("Spanish: and in the shell's own words", i18n(true).launchError(error) === lost[2], i18n(true).launchError(error));
    check("English: a refusal the interface knows keeps its text",
          i18n(false).launchError({ code: "busy", message: "Ya hay un juego en marcha" }) === "A game is already running.");
}

startUpChecks();
awakeChecks();
panelChecks();

console.log("interface logic: " + passed + " passed, " + failed + " failed");
process.exit(failed === 0 ? 0 : 1);
