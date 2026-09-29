/* Drawing primitives over the RAM back buffer: fills, discs and a small
 * embedded 5x7 font scaled up, enough for a counter and two lines of text. */
#ifndef MUN_DRAW_H
#define MUN_DRAW_H

#include "fb.h"

void draw_clear(struct fb *fb, uint32_t colour);
void draw_rect(struct fb *fb, int x, int y, int w, int h, uint32_t colour);
void draw_disc(struct fb *fb, int cx, int cy, int r, uint32_t colour);
void draw_text(struct fb *fb, int x, int y, int scale, const char *text, uint32_t colour);
int draw_text_width(int scale, const char *text);

#endif
