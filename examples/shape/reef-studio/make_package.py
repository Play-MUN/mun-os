#!/usr/bin/env python3
"""Makes the reef package (../reef) from the bake (render/).

    node bake.mjs                 # the images and render/layout.json
    python3 make_package.py       # ../reef: shape.json, the images, the sounds

The bake says where everything is (in the canvas's pixels, 1920 x 1080, and
depths, 0 near to 1 far) and how the sea moves; this script turns that into
the package's motion (mun-shape/1.1, draft: docs/shape-motion.md) and adds
what the sea does when the console speaks to it. The choices a creator is
most likely to change are the constants below. Python's standard library
only.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER = HERE / "render"
OUT = HERE.parent / "reef"
SEA = HERE.parent / "sea"          # the sounds are the sea sample's

# ---- choices

RATE = 20                           # steps a second (what moves is drawn between them on every display frame)
TRANSITION = {"in": "fade", "out": "fade", "seconds": 1.6}
PALETTE = {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E", "plate": "#04283A", "text": "#EFFFFF", "accent": "#F2B85C"}
GLOW = "#9FE7F2"
# the school that answers Play: near the disc, the amber one
ANSWERS = "fish-near"
# with Play in focus it mills round below the card object: (dx, dy, depth)
# points of a small loop, measured from the disc wherever Home draws it,
# gone round in so many seconds; low enough that the card object does not
# hide it, high enough that it stays above the console's bottom line (in
# both of Home's designs), so its round is mostly in depth
GATHER = {"points": [[-230, 268, 0.16], [0, 278, 0.07], [230, 268, 0.16], [0, 258, 0.3]], "seconds": 9}
# bubbles: how many may rise at once, and how many come a second
BUBBLES = {"max": 90, "rate": 7}
# the sea's snow: far and near
SNOW = {"far": {"max": 130, "rate": 7}, "near": {"max": 60, "rate": 3}}


def load():
    if not (RENDER / "layout.json").is_file():
        sys.exit("make_package: no render/layout.json: run node bake.mjs first")
    return json.loads((RENDER / "layout.json").read_text())


def clips(spec, mode=None):
    out = {}
    for c in spec:
        out[c["name"]] = [c["first"], c["count"], c["fps"]] + ([mode] if mode else [])
    return out


def main():
    L = load()
    if OUT.exists():
        shutil.rmtree(OUT)
    for folder in ("card", "posters", "planes", "sheets", "sfx"):
        (OUT / folder).mkdir(parents=True)

    def take(name, folder):
        shutil.copyfile(RENDER / f"{name}.png", OUT / folder / f"{name}.png")
        return f"{folder}/{name}.png"

    window = take("window", "card")
    poster = take("poster", "posters")
    for wav in sorted((SEA / "sfx").glob("*.wav")):
        shutil.copyfile(wav, OUT / "sfx" / wav.name)

    A = L["actors"]
    sheets, actors, paths, states = {}, {}, {}, {}

    def sheet(name, spec, mode=None, views=None):
        sheets[name] = {"image": take(name, "sheets"), "frame": spec["frame"], "clips": clips(spec["clips"], mode)}
        if views:
            sheets[name]["views"] = views

    # The light on the sand: per slab of depth, a loop of frames dissolving
    # into each other, breathing.
    for name in ("light-far", "light-mid", "light-near"):
        a = A[name]
        sheet(name, a, "blend")
        actors[name] = {"sheet": name, "clip": "shimmer", "at": a["at"], "z": a["z"], "keep": "ambient", "rest": "breathe"}
        states[f"{name}.breathe"] = {"vary": {"opacity": [a["pulse"]["min"], a["pulse"]["max"], a["pulse"]["period"]]}}

    # The kelp: each strand where it grows, bending from its foot.
    k = A["kelp"]
    sheet("kelp", k)
    actors["kelp"] = {"sheet": "kelp", "at": k["at"], "z": k["z"], "keep": "ambient",
                      "copies": {"places": k["places"], "clips": [c["name"] for c in k["clips"]]},
                      "bend": {"amount": k["bend"]["amount"], "period": k["bend"]["period"], "wave": k["bend"]["wave"], "follow": 0}}

    # The rays: each where it falls, swinging a little, flickering.
    r = A["rays"]
    sheet("rays", r)
    actors["rays"] = {"sheet": "rays", "at": r["at"], "z": r["z"], "keep": "ambient", "rest": "swing",
                      "copies": {"places": r["places"], "clips": [c["name"] for c in r["clips"]]}}
    states["rays.swing"] = {"vary": {"rotate": [-r["swing"]["degrees"], r["swing"]["degrees"], r["swing"]["period"]],
                                     "opacity": [r["flicker"]["min"], r["flicker"]["max"], r["flicker"]["period"]]}}

    # The schools: a formation going round in depth, each fish facing its
    # heading, its tail beating, drawing away from the shark.
    for name in ("fish-far", "fish-mid", "fish-near"):
        a = A[name]
        sheet(name, a, views=a["views"])
        paths[f"{name}-round"] = {"points": L["paths"][a["path"]]["points"], "seconds": L["paths"][a["path"]]["seconds"],
                                  "loop": True, "pace": "even"}
        actors[name] = {"sheet": name, "clip": "swim-a", "at": a["at"], "z": a["z"], "face": "views", "bank": 1,
                        "keep": "identity" if name == ANSWERS else "ambient", "rest": "swim",
                        "copies": {"places": a["places"], "clips": ["swim-a", "swim-b", "swim-c"], "lag": 0.09,
                                   "wobble": a["wobble"]},
                        "avoid": {"actor": "shark", "radius": a["avoid"]["radius"], "push": a["avoid"]["push"]}}
        states[f"{name}.swim"] = {"path": f"{name}-round"}

    # The shark, far behind, going round slowly, its body swaying.
    s = A["shark"]
    sheet("shark", s, "blend", views=s["views"])
    paths["shark-round"] = {"points": L["paths"]["shark-round"]["points"], "seconds": L["paths"]["shark-round"]["seconds"],
                            "loop": True, "pace": "even"}
    actors["shark"] = {"sheet": "shark", "clip": "swim", "at": s["at"], "z": s["z"], "face": "views", "bank": 0.6,
                       "keep": "ambient", "rest": "round"}
    states["shark.round"] = {"path": "shark-round"}

    # The crabs, sideways a little way and back, rocking.
    for name in ("crab-0", "crab-1"):
        a = A[name]
        sheets[name] = {"image": take(name, "sheets"), "frame": a["frame"]}
        w = a["walk"]
        actors[name] = {"sheet": name, "at": a["at"], "z": a["z"], "keep": "detail", "rest": "walk"}
        states[f"{name}.walk"] = {"vary": {"x": [w["x"][0], w["x"][1], w["period"], "sine", w["phase"]],
                                           "y": [w["y"][0], w["y"][1], w["period"], "sine", w["phase"]],
                                           "rotate": [-a["rock"]["degrees"], a["rock"]["degrees"], a["rock"]["period"]]}}

    # The jellyfish, pulsing, rising slowly, fading out at the top of their
    # rise and in again below.
    j = A["jellies"]
    sheet("jellies", j, "blend")
    actors["jellies"] = {"sheet": "jellies", "clip": "pulse-0", "at": j["at"], "z": j["z"], "keep": "ambient", "rest": "drift",
                         "copies": {"places": j["places"], "clips": [c["name"] for c in j["clips"]], "lag": 0.6}}
    half = round(j["rise"] / 2, 1)
    states["jellies.drift"] = {"vary": {"y": [half, -half, j["riseSeconds"], "saw"],
                                        "opacity": [0, 1, j["riseSeconds"], "fade"],
                                        "x": [-j["sway"], j["sway"], j["swaySeconds"]]}}

    # What the console does to the sea: with Play in focus the amber school
    # mills round below the disc and the disc's light grows; when Play is
    # pressed it dives into the disc; back from the game, it comes out and
    # goes round again.
    paths["gather"] = {"points": GATHER["points"], "origin": "disc", "seconds": GATHER["seconds"], "loop": True,
                       "pace": "even"}
    paths["dive"] = {"to": "disc", "seconds": 1.4, "ease": "in"}
    states[f"{ANSWERS}.gather"] = {"path": "gather"}
    states[f"{ANSWERS}.dive"] = {"path": "dive", "then": "gone"}
    states[f"{ANSWERS}.gone"] = {"hidden": True}

    e = L["emitters"]
    pull = {"radius": e["pull"]["radius"], "speed": e["pull"]["speed"], "swirl": e["pull"]["swirl"], "swallow": e["pull"]["swallow"]}
    sheets["bubble"] = {"image": take("bubble", "sheets")}
    sheets["snow"] = {"image": take("snow", "sheets")}
    rise = e["bubbles"]["rise"]
    emitters = {
        "bubbles": {"sheet": "bubble", "region": [-100, 650, 2120, 330], "rate": BUBBLES["rate"], "max": BUBBLES["max"],
                    "life": [round(1080 / rise[1], 1), round(1080 / rise[0], 1)], "velocity": [0, -round(sum(rise) / 2, 1)],
                    "spread": 6, "sway": 8, "fade": 0.3, "z": e["bubbles"]["z"], "pull": pull},
        "snow-far": {"sheet": "snow", "region": [-100, -60, 2120, 1100], "rate": SNOW["far"]["rate"], "max": SNOW["far"]["max"],
                     "life": [14, 22], "velocity": [0, e["snow"]["sink"][0]], "sway": 3, "fade": 0.5, "z": 0.8, "pull": pull},
        "snow-near": {"sheet": "snow", "region": [-100, -60, 2120, 1100], "rate": SNOW["near"]["rate"], "max": SNOW["near"]["max"],
                      "life": [8, 14], "velocity": [0, e["snow"]["sink"][1] * 1.6], "sway": 7, "fade": 0.5, "z": 0.15, "pull": pull},
    }
    lights = {"glow": {"effect": "glow", "at": "disc", "colour": GLOW, "radius": round(L["disc"]["radius"] * 1.7), "opacity": 0.22,
                       "z": round(L["disc"]["z"], 3)}}
    states["glow.bright"] = {"opacity": 0.45}

    document = {
        "format": "mun-shape/1.1",
        # the draft's revision this package is written for (docs/shape-motion.md)
        "motion_draft": 1,
        "palette": PALETTE,
        "card": {"window": window, "shape": "card", "glow": GLOW},
        "world": {"backdrop": {"image": poster}},
        "surfaces": {"entries": {"material": "glass"}, "panel": {"material": "glass"}},
        "transition": TRANSITION,
        "sounds": {n: f"sfx/{n}.wav" for n in ("move", "enter", "back", "insert")},
        "scene": {
            "look": "pixel", "rate": RATE, "poster": poster,
            "depth": {"far": L["depth"]["share"], "span": L["depth"]["span"]},
            "sway": {"x": L["sway"]["x"], "y": L["sway"]["y"], "period": L["sway"]["period"], "pivot": L["sway"]["pivot"]},
            "planes": [{"image": take(p["name"], "planes"), "z": p["z"], "at": p["at"]}
                       for p in sorted(L["planes"], key=lambda p: -p["z"])],
        },
        "sheets": sheets, "actors": actors, "paths": paths, "states": states, "emitters": emitters, "lights": lights,
        "react": {
            "focus.play": {"do": {ANSWERS: "gather", "glow": "bright"}},
            "focus.away": {ANSWERS: "swim", "glow": "on"},
            "launch": {ANSWERS: "dive", "glow": "bright"},
            "return": {ANSWERS: "swim", "glow": "on"},
        },
    }
    (OUT / "shape.json").write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"{OUT}: {len(actors)} actors, {sum(len(a.get('copies', {}).get('places', [0])) for a in actors.values())} figures, "
          f"{len(sheets)} sheets, {size / 1048576:.1f} MiB")


if __name__ == "__main__":
    main()
