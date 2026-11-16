#version 440

// A surface's quad (src/surfacenode.h): passes the item's coordinates on
// to box.frag, which draws the surface from them. The uniform block
// is that shader's.

layout(location = 0) in vec2 vertexPosition;
layout(location = 0) out vec2 position;

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

out gl_PerVertex { vec4 gl_Position; };

void main()
{
    position = vertexPosition;
    gl_Position = qt_Matrix * vec4(vertexPosition, 0.0, 1.0);
}
