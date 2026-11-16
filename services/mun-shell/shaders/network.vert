#version 440

// Home's network of light on the graphics path (src/networknode.h). The
// geometry is built once; this places each vertex for the current frame
// from a few numbers, as Backdrop::paintFrame() places its lines, and gives
// each line the light hairline.h gives it: the vertex on the line carries
// its alpha, the two beside it (`side` -1 and 1) none, one device pixel
// away along the minor axis.

layout(location = 0) in vec2 vertexRest;    // its place at rest (an orbit's place)
layout(location = 1) in vec4 vertexKind;    // kind, index, side, value

layout(location = 0) out vec4 colour;       // premultiplied
layout(location = 1) out float dashAt;      // along an orbit, its dashes' offset included
layout(location = 2) out float dashed;      // 1 on an orbit

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;          // orb x, orb y, one device pixel in item units, opacity
    vec4 motion;        // the rings' drift (0-150), the spokes' turn, the waves' wobble phase, the floor's phase (0-1)
    vec4 extent;        // width, height, the two orbits' dash offsets (0-12)
    vec4 ripples[8];    // a wave's front and strength (1 - its progress; 0: none)
};

out gl_PerVertex { vec4 gl_Position; };

const vec3 kGrey = vec3(218.0, 215.0, 209.0) / 255.0;
const vec3 kPatina = vec3(127.0, 179.0, 163.0) / 255.0;

// The waves' light at radius `r` (Backdrop::wave()).
float wave(float r)
{
    float light = 0.0;
    for (int i = 0; i < 8; ++i) {
        if (ripples[i].y > 0.0) {
            float d = (r - ripples[i].x) / 130.0;
            light += exp(-d * d) * ripples[i].y * 0.55;
        }
    }
    return min(1.0, light);
}

// Across a line moving along `along` (a unit vector): the step that moves
// `reach` along its minor axis.
vec2 across(vec2 along, float reach)
{
    return vec2(-along.y, along.x) * (reach * max(abs(along.x), abs(along.y)));
}

void main()
{
    int kind = int(vertexKind.x + 0.5);
    float index = vertexKind.y;
    float side = vertexKind.z;
    vec2 orb = view.xy;
    float pixel = view.z;
    // A line one item unit wide (the orbits, the floor): its light spread
    // over at least one device pixel each side.
    float unitHalf = max(pixel, 1.0);
    float unitGain = 1.0 / unitHalf;
    vec2 position;
    vec3 rgb = kGrey;
    float alpha;
    dashAt = 0.0;
    dashed = 0.0;
    if (kind == 0) {
        // Ring `index`, at angle `value`.
        float r = 230.0 + index * 150.0 + motion.x;
        float light = wave(r);
        float th = vertexKind.w;
        vec2 radial = vec2(cos(th), sin(th));
        float shown = r;
        alpha = 0.08 * (1.0 - index / 10.0);
        if (light > 0.02) {
            shown += 5.0 * light * sin(th * 9.0 + motion.z);
            alpha = min(1.0, (alpha + 0.12 * light) * (1.0 + 0.5 * light));
            rgb = vec3(218.0 - 91.0 * light, 215.0 - 36.0 * light, 209.0 - 46.0 * light) / 255.0;
        }
        position = orb + radial * shown + across(vec2(-radial.y, radial.x), pixel) * side;
    } else if (kind == 1 || kind == 2) {
        // Spoke `index`, at its near end (`value` 0) or far end (1); or, kind
        // 2, the stretch of it that wave `value / 2` lights, its ends as
        // `value`'s parity.
        float a = index / 36.0 * 6.283 + motion.y;
        vec2 dir = vec2(cos(a), sin(a));
        float r;
        if (kind == 1) {
            r = vertexKind.w > 0.5 ? 1800.0 : 240.0;
            alpha = vertexKind.w > 0.5 ? 0.0 : 0.10;
        } else {
            float slot = floor(vertexKind.w / 2.0 + 0.25);
            vec4 ripple = ripples[int(slot)];
            r = ripple.x + (vertexKind.w - 2.0 * slot > 0.5 ? 60.0 : -60.0);
            alpha = min(1.0, 0.10 * ripple.y * 1.5);
            rgb = kPatina;
            // No wave in this slot: every vertex at the orb, nothing drawn.
            if (ripple.y <= 0.0) {
                r = 0.0;
                side = 0.0;
                alpha = 0.0;
            }
        }
        position = orb + dir * r + across(dir, pixel) * side;
    } else if (kind == 3) {
        // Orbit `index`: placed once, `value` its length so far.
        position = vertexRest;
        alpha = (0.14 - index * 0.04) * unitGain;
        dashAt = vertexKind.w + (index < 0.5 ? extent.z : extent.w);
        dashed = 1.0;
    } else {
        // Floor line `index` of 14, at its left end (`value` 0) or right (1).
        float u = (index + motion.w) / 14.0;
        float y = 880.0 + (extent.y - 880.0) * u * u;
        position = vec2(vertexKind.w * extent.x, y + side * unitHalf);
        alpha = 0.08 * u * unitGain;
    }
    if (side != 0.0)
        alpha = 0.0;
    colour = vec4(rgb * alpha, alpha);
    gl_Position = qt_Matrix * vec4(position, 0.0, 1.0);
}
