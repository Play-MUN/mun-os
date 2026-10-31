#version 440

// Home's network of light (network.vert).

layout(location = 0) in vec4 colour;
layout(location = 1) in highp float dashAt;
layout(location = 2) in float dashed;

layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;
    vec4 motion;
    vec4 extent;
    vec4 ripples[8];
};

void main()
{
    // An orbit shows 2 of every 12 units along it (QPainter's dash pattern
    // {2, 10}), counted at each pixel as its thin-line stroker counts them.
    float shown = dashed > 0.5 && mod(dashAt, 12.0) >= 2.0 ? 0.0 : 1.0;
    fragColor = colour * (shown * view.w);
}
