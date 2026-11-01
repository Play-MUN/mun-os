#version 440
// The waves (src/orbitalbackdrop.h): each wave's fill, darkening downwards,
// and its edge, a thin line with a glow, added to what is beneath. The front
// wave's edge is lit by the eclipse on the left and again by the panel on
// the right; under its lit part the fill takes a warm reflection. The back
// wave is a veil a little lighter than the night, its edge faint.
layout(location = 0) in vec4 v;     // x, distance from the curve (down positive), wave, part
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;    // opacity, time (s), motion (0 or 1), one device pixel
    vec4 shape;   // the bottom of the screen, unused
};

float rimLight(float x)
{
    float left = exp(-pow((x - 170.0) / 230.0, 2.0));
    float tail = 0.30 * exp(-pow((x - 560.0) / 460.0, 2.0));
    float right = 0.85 * smoothstep(1350.0, 1880.0, x);
    return 0.30 + 1.25 * left + tail + right;
}

void main()
{
    float x = v.x;
    float d = v.y;
    bool front = v.z > 0.5;
    float px = view.w;
    if (v.w < 0.5) {
        // Fill: antialiased along the curve over one device pixel.
        float edge = clamp(d / px + 0.5, 0.0, 1.0);
        vec3 colour;
        float alpha;
        if (front) {
            colour = mix(vec3(0.137, 0.141, 0.149), vec3(0.051, 0.055, 0.063), clamp(d / 260.0, 0.0, 1.0));
            colour += 0.16 * exp(-pow((x - 230.0) / 330.0, 2.0)) * exp(-max(d, 0.0) / 120.0) * vec3(0.91, 0.63, 0.43);
            alpha = edge;
        } else {
            colour = mix(vec3(0.118, 0.122, 0.129), vec3(0.057, 0.061, 0.069), clamp(d / 400.0, 0.0, 1.0));
            alpha = 0.85 * edge * smoothstep(700.0, 820.0, x);
        }
        fragColor = vec4(colour * alpha, alpha) * view.x;
        return;
    }
    // Edge: a line about a pixel wide and two glows, added.
    float line = exp(-pow(d / max(1.05, px), 2.0));
    vec3 light;
    if (front) {
        float glow = 0.30 * exp(-abs(d) / 7.0) + 0.12 * exp(-abs(d) / 26.0);
        light = (line * vec3(1.0, 0.886, 0.745) + glow * vec3(0.913, 0.690, 0.521)) * rimLight(x);
    } else {
        float faint = (0.35 * line + 0.08 * exp(-abs(d) / 10.0)) * smoothstep(700.0, 900.0, x);
        light = faint * vec3(0.95, 0.85, 0.75);
    }
    fragColor = vec4(light, 0.0) * view.x;
}
