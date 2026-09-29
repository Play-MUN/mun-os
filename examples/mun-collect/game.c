#include "game.h"

static unsigned next_rand(unsigned *state)
{
    *state = *state * 1103515245u + 12345u;
    return (*state >> 16) & 0x7fff;
}

void game_init(struct game *g, float width, float height, unsigned seed)
{
    unsigned state = seed ? seed : 1u;
    g->width = width;
    g->height = height;
    g->size = height * 0.06f;
    g->speed = height * 0.55f;
    g->px = width * 0.5f;
    g->py = height * 0.5f;
    g->collected = 0;
    g->finished = 0;
    g->elapsed = 0.0f;
    for (int i = 0; i < MUN_ITEMS; i++) {
        /* Keep discs away from the edges and from the starting position. */
        float x, y;
        do {
            x = width * (0.12f + 0.76f * (next_rand(&state) % 1000) / 1000.0f);
            y = height * (0.18f + 0.66f * (next_rand(&state) % 1000) / 1000.0f);
        } while ((x - g->px) * (x - g->px) + (y - g->py) * (y - g->py) < (height * 0.2f) * (height * 0.2f));
        g->items[i].x = x;
        g->items[i].y = y;
        g->items[i].taken = 0;
    }
}

void game_update(struct game *g, int dx, int dy, float dt)
{
    float half = g->size * 0.5f;
    float radius = g->size * 0.45f;
    if (g->finished)
        return;
    g->elapsed += dt;
    g->px += dx * g->speed * dt;
    g->py += dy * g->speed * dt;
    if (g->px < half) g->px = half;
    if (g->py < half) g->py = half;
    if (g->px > g->width - half) g->px = g->width - half;
    if (g->py > g->height - half) g->py = g->height - half;
    for (int i = 0; i < MUN_ITEMS; i++) {
        float ddx = g->items[i].x - g->px, ddy = g->items[i].y - g->py;
        if (!g->items[i].taken && ddx * ddx + ddy * ddy < (half + radius) * (half + radius)) {
            g->items[i].taken = 1;
            g->collected++;
        }
    }
    if (g->collected == MUN_ITEMS)
        g->finished = 1;
}
