/* MUN Collect game logic: a player square, a handful of copper discs to
 * collect, a counter and a finished state. Pure data and rules; no drawing,
 * no input devices, so the rules are testable without a screen. */
#ifndef MUN_GAME_H
#define MUN_GAME_H

#define MUN_ITEMS 5

struct item { float x, y; int taken; };

struct game {
    float width, height;   /* playfield in pixels */
    float px, py;          /* player centre */
    float size;            /* player side */
    float speed;           /* pixels per second */
    struct item items[MUN_ITEMS];
    int collected;
    int finished;
    float elapsed;
};

void game_init(struct game *g, float width, float height, unsigned seed);
/* dx, dy in {-1,0,1}; dt in seconds. */
void game_update(struct game *g, int dx, int dy, float dt);

#endif
