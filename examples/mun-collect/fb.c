#include "fb.h"

#include <fcntl.h>
#include <linux/fb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

int fb_open(struct fb *fb, const char *path, char *err, size_t errlen)
{
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;

    memset(fb, 0, sizeof *fb);
    fb->fd = open(path, O_RDWR | O_CLOEXEC);
    if (fb->fd < 0) {
        snprintf(err, errlen, "open %s failed", path);
        return -1;
    }
    if (ioctl(fb->fd, FBIOGET_VSCREENINFO, &var) < 0 || ioctl(fb->fd, FBIOGET_FSCREENINFO, &fix) < 0) {
        snprintf(err, errlen, "framebuffer ioctl failed");
        close(fb->fd);
        return -1;
    }
    /* Only 32 bpp true colour is supported; refuse anything else loudly. */
    if (var.bits_per_pixel != 32 || fix.type != FB_TYPE_PACKED_PIXELS || fix.visual != FB_VISUAL_TRUECOLOR) {
        snprintf(err, errlen, "unsupported framebuffer: %u bpp, type %u, visual %u",
                 var.bits_per_pixel, fix.type, fix.visual);
        close(fb->fd);
        return -1;
    }
    if (var.xres < 640 || var.yres < 360 || fix.line_length < var.xres * 4) {
        snprintf(err, errlen, "unsupported geometry: %ux%u, line %u", var.xres, var.yres, fix.line_length);
        close(fb->fd);
        return -1;
    }
    fb->width = var.xres;
    fb->height = var.yres;
    fb->bpp = var.bits_per_pixel;
    fb->line_length = fix.line_length;
    fb->roff = var.red.offset;
    fb->goff = var.green.offset;
    fb->boff = var.blue.offset;
    fb->map_size = (size_t)fix.line_length * var.yres_virtual;
    if (fb->map_size < (size_t)fix.line_length * fb->height)
        fb->map_size = (size_t)fix.line_length * fb->height;
    fb->mem = mmap(NULL, fb->map_size, PROT_READ | PROT_WRITE, MAP_SHARED, fb->fd, 0);
    if (fb->mem == MAP_FAILED) {
        snprintf(err, errlen, "mmap framebuffer failed");
        close(fb->fd);
        return -1;
    }
    fb->back = calloc((size_t)fb->width * fb->height, sizeof(uint32_t));
    if (!fb->back) {
        snprintf(err, errlen, "out of memory");
        munmap(fb->mem, fb->map_size);
        close(fb->fd);
        return -1;
    }
    return 0;
}

void fb_close(struct fb *fb)
{
    if (fb->back)
        free(fb->back);
    if (fb->mem && fb->mem != MAP_FAILED)
        munmap(fb->mem, fb->map_size);
    if (fb->fd >= 0)
        close(fb->fd);
    memset(fb, 0, sizeof *fb);
    fb->fd = -1;
}

void fb_present(struct fb *fb)
{
    /* write(2) rather than the mmap: on the console's virtio-gpu fbdev the
     * write path marks the damaged area and reaches the display immediately,
     * whereas plain stores into the mapping were not being flushed. The
     * mapping stays as a fallback if the write path fails. */
    if (fb->line_length == fb->width * 4) {
        size_t total = (size_t)fb->line_length * fb->height, done = 0;
        const uint8_t *src = (const uint8_t *)fb->back;
        while (done < total) {
            ssize_t n = pwrite(fb->fd, src + done, total - done, (off_t)done);
            if (n <= 0)
                break;
            done += (size_t)n;
        }
        if (done == total)
            return;
    }
    for (unsigned y = 0; y < fb->height; y++) {
        const uint8_t *row = (const uint8_t *)(fb->back + (size_t)y * fb->width);
        if (pwrite(fb->fd, row, (size_t)fb->width * 4, (off_t)y * fb->line_length) != (ssize_t)((size_t)fb->width * 4))
            memcpy(fb->mem + (size_t)y * fb->line_length, row, (size_t)fb->width * 4);
    }
}

uint32_t fb_rgb(const struct fb *fb, uint8_t r, uint8_t g, uint8_t b)
{
    return ((uint32_t)r << fb->roff) | ((uint32_t)g << fb->goff) | ((uint32_t)b << fb->boff);
}
