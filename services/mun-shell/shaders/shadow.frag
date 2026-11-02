#version 440

// A BoxShadow (src/cssbox.h) on the graphics path: each outer shadow is its
// shape (the box moved and spread) blurred by a Gaussian of standard
// deviation blur / 2, painted last to first, and nothing under the box
// itself, as BoxShadow::paint() paints them. The blur is computed, not
// sampled: exactly along x (the error function), by eight samples along y,
// with the rounded corners following the row (after Evan Wallace's rounded
// box shadows). Colours are premultiplied.

layout(location = 0) in vec2 position;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;          // opacity, one device pixel, shadows, unused
    vec4 box;           // the box: x, y, width, height
    vec4 boxRadii;
    vec4 shapes[4];     // each shadow's shape: x, y, width, height
    vec4 shapeRadii[4];
    vec4 sigmas;        // each shadow's standard deviation (0: sharp)
    vec4 colors[4];
};

float roundedBox(vec2 p, vec4 rect, vec4 r)
{
    vec2 extent = rect.zw * 0.5;
    vec2 q = p - (rect.xy + extent);
    float corner = q.x > 0.0 ? (q.y > 0.0 ? r.z : r.y) : (q.y > 0.0 ? r.w : r.x);
    vec2 d = abs(q) - extent + corner;
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - corner;
}

float coverage(float distance)
{
    return clamp(0.5 - distance / view.y, 0.0, 1.0);
}

vec2 erf2(vec2 x)
{
    vec2 s = sign(x), a = abs(x);
    x = 1.0 + (0.278393 + (0.230389 + 0.078108 * (a * a)) * a) * a;
    x *= x;
    return s - s / (x * x);
}

// The share of row `y` (from the shape's centre) of the shape within
// reach of x, blurred along x.
float blurredRow(float x, float y, float sigma, float corner, vec2 extent)
{
    float delta = min(extent.y - corner - abs(y), 0.0);
    float curved = extent.x - corner + sqrt(max(0.0, corner * corner - delta * delta));
    vec2 integral = 0.5 + 0.5 * erf2((x + vec2(-curved, curved)) * (0.7071067811865476 / sigma));
    return integral.y - integral.x;
}

float blurredBox(vec2 p, vec4 rect, vec4 r, float sigma)
{
    vec2 extent = rect.zw * 0.5;
    vec2 q = p - (rect.xy + extent);
    float corner = min(q.x > 0.0 ? (q.y > 0.0 ? r.z : r.y) : (q.y > 0.0 ? r.w : r.x), min(extent.x, extent.y));
    float low = q.y - extent.y, high = q.y + extent.y;
    float start = clamp(-3.0 * sigma, low, high), end = clamp(3.0 * sigma, low, high);
    float step = (end - start) / 8.0;
    float y = start + step * 0.5;
    float value = 0.0;
    for (int i = 0; i < 8; ++i) {
        float weight = exp(-(y * y) / (2.0 * sigma * sigma)) / (2.5066282746310002 * sigma);
        value += blurredRow(q.x, q.y - y, sigma, corner, extent) * weight * step;
        y += step;
    }
    return value;
}

void main()
{
    vec4 result = vec4(0.0);
    for (int i = 3; i >= 0; --i) {
        if (float(i) >= view.z)
            continue;
        float shown = sigmas[i] > 0.0 ? blurredBox(position, shapes[i], shapeRadii[i], sigmas[i])
                                      : coverage(roundedBox(position, shapes[i], shapeRadii[i]));
        result = colors[i] * shown + result * (1.0 - colors[i].a * shown);
    }
    // Never under the box that casts it.
    result *= 1.0 - coverage(roundedBox(position, box, boxRadii));
    fragColor = result * view.x;
}
