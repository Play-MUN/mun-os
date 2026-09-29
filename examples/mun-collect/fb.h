/* Framebuffer output for MUN Collect: opens /dev/fb0, validates the mode it
 * actually has (resolution, 32 bpp, line length) and blits a RAM back buffer.
 * Nothing about the display is assumed; everything is queried. */
#ifndef MUN_FB_H
#define MUN_FB_H

#include <stddef.h>
#include <stdint.h>

struct fb {
    int fd;
    uint8_t *mem;        /* mapped device memory */
    uint32_t *back;      /* RAM back buffer, width*height pixels */
    unsigned width, height;
    unsigned line_length; /* bytes per device row */
    unsigned bpp;
    unsigned roff, goff, boff; /* channel bit offsets, from the driver */
    size_t map_size;
};

int fb_open(struct fb *fb, const char *path, char *err, size_t errlen);
void fb_close(struct fb *fb);
void fb_present(struct fb *fb);
uint32_t fb_rgb(const struct fb *fb, uint8_t r, uint8_t g, uint8_t b);

#endif
