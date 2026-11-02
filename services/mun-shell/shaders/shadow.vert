#version 440

// A surface's quad (src/surfacenode.h): passes the item's coordinates on
// to shadow.frag, which draws the surface from them. The uniform block
// is that shader's.

layout(location = 0) in vec2 vertexPosition;
layout(location = 0) out vec2 position;

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

out gl_PerVertex { vec4 gl_Position; };

void main()
{
    position = vertexPosition;
    gl_Position = qt_Matrix * vec4(vertexPosition, 0.0, 1.0);
}
