#version 440
// The waves (src/orbitalbackdrop.h). Every vertex is at an x and either on
// a strip around its wave's curve (an offset from it, in pixels) or at the
// bottom of the screen; the curve's height at x comes from the time, so the
// mesh is built once and the GPU places it on every frame. The fragment
// shader gets the vertical distance from the curve, which the strip and the
// fill interpolate exactly (it is linear in y).
layout(location = 0) in vec2 rest;        // x, and y at rest (the renderer's bounds)
layout(location = 1) in vec4 attr;        // offset (or 1e4: the bottom), wave (0 back, 1 front), part (0 fill, 1 edge), unused
layout(location = 0) out vec4 v;          // x, distance from the curve, wave, part
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;    // opacity, time (s), motion (0 or 1), one device pixel
    vec4 shape;   // the bottom of the screen, unused
};

float curve(float x, vec4 xs, vec4 ys)
{
    if (x <= xs.y) {
        float u = clamp((x - xs.x) / (xs.y - xs.x), 0.0, 1.0);
        return mix(ys.x, ys.y, 0.5 - 0.5 * cos(3.14159265 * u));
    }
    if (x <= xs.z) {
        float u = (x - xs.y) / (xs.z - xs.y);
        return mix(ys.y, ys.z, 0.5 - 0.5 * cos(3.14159265 * u));
    }
    float u = clamp((x - xs.z) / (xs.w - xs.z), 0.0, 1.0);
    return mix(ys.z, ys.w, 0.5 - 0.5 * cos(3.14159265 * u));
}

void main()
{
    float t = view.y * view.z;
    float x = rest.x;
    // A slow drift of the whole shape, and each crest and trough swaying.
    float xs = x + 18.0 * sin(t * 0.09);
    vec4 sway = vec4(sin(t * 0.37), sin(t * 0.29 + 1.3), sin(t * 0.23 + 2.1), sin(t * 0.31 + 0.7));
    float y0 = attr.y > 0.5
        ? curve(xs, vec4(0.0, 494.0, 1148.0, 1920.0), vec4(792.0, 893.0, 817.0, 655.0) + 7.0 * sway)
        : curve(xs, vec4(700.0, 1150.0, 1500.0, 1920.0), vec4(900.0, 805.0, 795.0, 610.0) + 6.0 * sway.wzyx);
    float y = attr.x > 5000.0 ? shape.x : y0 + attr.x;
    v = vec4(xs, y - y0, attr.y, attr.z);
    gl_Position = qt_Matrix * vec4(x, y, 0.0, 1.0);
}
