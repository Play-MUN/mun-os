#version 440

// A Box (src/cssbox.h) on the graphics path: its colour, then its gradient,
// then its inset shadows (blur 0) in reverse, inside its rounded corners, as
// Box::paint() paints them. Colours are premultiplied.

layout(location = 0) in vec2 position;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;          // opacity, one device pixel, gradient kind (0 none, 1 linear, 2 radial), insets
    vec4 rect;          // x, y, width, height
    vec4 radii;         // top-left, top-right, bottom-right, bottom-left
    vec4 color;
    vec4 line;          // linear: start x, y, end x, y; radial: centre x, y, radius
    vec4 stopAt;        // up to four stops' positions (unused: 2)
    vec4 stops[4];
    vec4 holes[4];      // each inset's hole: x, y, width, height
    vec4 holeRadii[4];
    vec4 insetColors[4];
};

// The signed distance from a rounded rectangle (negative inside).
float roundedBox(vec2 p, vec4 box, vec4 r)
{
    vec2 extent = box.zw * 0.5;
    vec2 q = p - (box.xy + extent);
    float corner = q.x > 0.0 ? (q.y > 0.0 ? r.z : r.y) : (q.y > 0.0 ? r.w : r.x);
    vec2 d = abs(q) - extent + corner;
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - corner;
}

// How much of the pixel at `p` a shape covers, from its distance.
float coverage(float distance)
{
    return clamp(0.5 - distance / view.y, 0.0, 1.0);
}

vec4 over(vec4 top, vec4 under)
{
    return top + under * (1.0 - top.a);
}

vec4 gradientAt(vec2 p)
{
    float t;
    if (view.z > 1.5) {
        t = length(p - line.xy) / max(line.z, 0.0001);
    } else {
        vec2 along = line.zw - line.xy;
        t = dot(p - line.xy, along) / max(dot(along, along), 0.0001);
    }
    if (t <= stopAt.x)
        return stops[0];
    vec4 c = stops[0];
    for (int i = 1; i < 4; ++i) {
        if (stopAt[i] < 0.0)
            break;
        if (t <= stopAt[i]) {
            float span = stopAt[i] - stopAt[i - 1];
            return span > 0.0 ? mix(stops[i - 1], stops[i], (t - stopAt[i - 1]) / span) : stops[i];
        }
        c = stops[i];
    }
    return c;
}

void main()
{
    float inside = coverage(roundedBox(position, rect, radii));
    vec4 result = color;
    if (view.z > 0.5)
        result = over(gradientAt(position), result);
    result *= inside;
    for (int i = 3; i >= 0; --i) {
        if (float(i) >= view.w)
            continue;
        float band = holes[i].z > 0.0 && holes[i].w > 0.0 ? 1.0 - coverage(roundedBox(position, holes[i], holeRadii[i])) : 1.0;
        result = over(insetColors[i] * (band * inside), result);
    }
    fragColor = result * view.x;
}
