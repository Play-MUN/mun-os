#version 440
// The eclipse's quad (src/orbitalbackdrop.h): its corner of the screen, in
// the item's coordinates, passed on to the fragment shader.
layout(location = 0) in vec4 vertexCoord;
layout(location = 0) out vec2 pos;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    vec4 view;    // opacity, time (s), breath (0.82 to 1), one device pixel
    vec4 shape;   // the ring's centre x, y, its radius, unused
};
void main()
{
    pos = vertexCoord.xy;
    gl_Position = qt_Matrix * vertexCoord;
}
