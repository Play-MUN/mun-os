#version 440

// Shape motion's things on the graphics path (src/motionscene.cpp): runs of
// sprites from one image, each quad placed, turned and mirrored on the CPU,
// its opacity per vertex.

layout(location = 0) in vec2 vertexPosition;
layout(location = 1) in vec2 vertexTexCoord;
layout(location = 2) in float vertexOpacity;

layout(location = 0) out vec2 texCoord;
layout(location = 1) out float opacity;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
};

out gl_PerVertex { vec4 gl_Position; };

void main()
{
    texCoord = vertexTexCoord;
    opacity = vertexOpacity * qt_Opacity;
    gl_Position = qt_Matrix * vec4(vertexPosition, 0.0, 1.0);
}
