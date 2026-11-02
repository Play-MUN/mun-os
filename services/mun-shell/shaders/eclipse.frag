#version 440
// The eclipse (src/orbitalbackdrop.h): the outer ring's edge lit from the
// upper left and its warm halo, added to what is beneath (light does not
// darken). How much light each point of the edge gets follows its angle:
// none at nine o'clock, most towards ten, none again before twelve.
layout(location = 0) in vec2 pos;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;    // opacity, time (s), breath (0.82 to 1), one device pixel
    vec4 shape;   // the ring's centre x, y, its radius, unused
};

float light(float deg)
{
    float t = (deg - 182.0) / 86.0;
    if (t <= 0.0 || t >= 1.0)
        return 0.0;
    return pow(sin(3.14159265 * pow(t, 0.7)), 1.8);
}

void main()
{
    vec2 q = pos - shape.xy;
    float d = length(q) - shape.z;           // from the edge, outwards positive
    float deg = degrees(atan(q.y, q.x));     // the screen's y grows downwards
    if (deg < 0.0)
        deg += 360.0;
    float k = light(deg);
    // The halo hugs the edge, mostly outwards; it breathes.
    float halo = 0.55 * exp(-d * d / 50.0);
    if (d > 0.0)
        halo += 0.38 * exp(-d / 18.0) + 0.15 * exp(-d / 52.0);
    else
        halo += 0.10 * exp(d / 8.0);
    // The edge: a line widest where the light is strongest, antialiased over
    // one device pixel.
    float half_ = 0.6 + 1.2 * k;
    float line = clamp((half_ + view.w - abs(d)) / view.w, 0.0, 1.0) * min(1.0, k * 1.4);
    vec3 colour = halo * k * view.z * vec3(0.92, 0.62, 0.40) + line * vec3(1.0, 0.91, 0.80);
    fragColor = vec4(colour, 0.0) * view.x;
}
