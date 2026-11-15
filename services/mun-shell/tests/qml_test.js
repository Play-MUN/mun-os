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

startUpChecks();

console.log("interface logic: " + passed + " passed, " + failed + " failed");
process.exit(failed === 0 ? 0 : 1);
