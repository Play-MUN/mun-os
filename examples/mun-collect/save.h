/* MUN Collect saves: what travels on the Game Card and how the game reads it.
 *
 * The console writes an envelope (`mun-save/1`, or `neptune-save/1` on cards
 * of the earlier naming generation; docs/saves.md, docs/game-cards.md) around a
 * payload that only the game understands. This module serialises the payload,
 * parses an envelope read back from the console, and applies a compatible
 * payload to a freshly initialised game. It uses no library: the JSON here is
 * a bounded, strict subset parsed with a small recursive-descent scanner, and
 * anything unexpected is reported as damaged rather than guessed at. */
#ifndef MUN_SAVE_H
#define MUN_SAVE_H

#include <stddef.h>

#include "game.h"

#define SAVE_SCHEMA 1              /* bump when the payload shape changes */
#define SAVE_MAX_BYTES 65536       /* payload bound shared with the console */
#define SAVE_FILE_MAX_BYTES 81920  /* envelope bound the console applies */

enum save_status {
    SAVE_NONE = 0,          /* no save file: a new game */
    SAVE_OK,                /* envelope and payload parsed, schema understood */
    SAVE_DAMAGED,           /* not an envelope, truncated or inconsistent payload */
    SAVE_INCOMPATIBLE       /* a valid envelope whose schema this game cannot read */
};

struct saved {
    int schema;
    int recovered;          /* the console handed out save.json.prev because save.json was missing */
    unsigned seed;
    float x, y, elapsed;
    int taken[MUN_ITEMS];
    int collected;
};

/* Payload JSON for the current game state. Returns the length written, or -1
 * if `cap` is too small. The output is a single line without a newline. */
int save_serialize(const struct game *g, unsigned seed, char *out, size_t cap);

/* Parse an envelope of `len` bytes. On SAVE_OK, `out` is filled. Never reads
 * past `len`; never writes `out` unless it returns SAVE_OK (schema is set for
 * SAVE_INCOMPATIBLE so the game can say which one it met). */
enum save_status save_parse_envelope(const char *text, size_t len, struct saved *out);

/* Apply a parsed payload to a game already initialised with `saved->seed`,
 * clamping the position to the playfield. */
void save_apply(struct game *g, const struct saved *saved);

/* Human wording for the HUD, in the console's language. */
const char *save_status_text(enum save_status status);

#endif
