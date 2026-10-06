// The palette a console reads from a Game Card's cover when the card
// declares none (docs/shape.md, "Read level"). The algorithm is the card
// tool's (tools/mun-card/mun_card/shapetools.py, read_palette), in the same
// integer arithmetic, so the console and `mun-card shape check --cover`
// agree on every cover:
//
// 1. 8-bit RGB (a 16-bit sample keeps its high byte), transparency
//    composited over black, rounded half up;
// 2. box-averaged to 64 x 64;
// 3. sorted by 2126 R + 7152 G + 722 B, then R, G, B;
// 4. the bands deep 0-8, low 20-35, mid 50-70, hi 82-95, light 97-100 per
//    cent averaged, rounded half up;
// 5. the accent: the most saturated warm cell (red highest, saturation over
//    0.35, hue under 55 or over 330 degrees; the greatest max - min, the
//    first on a tie), scaled to a brightness of 235.
//
// Returns {light, hi, mid, low, deep, accent?} as "#RRGGBB" strings, or an
// empty map for an empty image. Any thread: it only reads the image.
#pragma once

#include <QImage>
#include <QVariantMap>

QVariantMap readPalette(const QImage &cover);
