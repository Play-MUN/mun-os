/* Host-side checks for the save module: round trip, damaged and incompatible
 * inputs. Built with the host compiler by `make test`; no framebuffer needed. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../game.h"
#include "../save.h"

static int failures;

#define CHECK(cond) do { if (!(cond)) { failures++; fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static void envelope(char *out, size_t cap, int schema, const char *payload)
{
    snprintf(out, cap, "{\"format\":\"neptune-save/1\",\"game\":\"mun.collect\",\"content_version\":\"0.1.0\","
                       "\"schema\":%d,\"saved_at\":\"2026-09-19T00:00:00Z\",\"payload\":%s}", schema, payload);
}

static void test_round_trip(void)
{
    struct game g, back;
    game_init(&g, 1920, 1080, 42u);
    game_update(&g, 1, 0, 0.5f);          /* move right for half a second */
    g.items[1].taken = 1; g.items[3].taken = 1; g.collected = 2;
    char payload[512], text[1024];
    int n = save_serialize(&g, 42u, payload, sizeof payload);
    CHECK(n > 0 && strlen(payload) == (size_t)n);
    CHECK(strstr(payload, "\"collected\":2") != NULL);
    CHECK(strstr(payload, "\"taken\":[0,1,0,1,0]") != NULL);
    envelope(text, sizeof text, SAVE_SCHEMA, payload);

    struct saved s;
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_OK);
    CHECK(s.seed == 42u && s.collected == 2 && s.taken[1] == 1 && s.taken[3] == 1 && s.taken[0] == 0);
    game_init(&back, 1920, 1080, s.seed);
    save_apply(&back, &s);
    CHECK(back.collected == 2 && back.items[1].taken && back.items[3].taken && !back.finished);
    CHECK((int)back.px == (int)g.px && (int)back.py == (int)g.py);
    CHECK(back.items[2].x == g.items[2].x && back.items[2].y == g.items[2].y);   /* same seed, same discs */

    for (int i = 0; i < MUN_ITEMS; i++) g.items[i].taken = 1;
    g.collected = MUN_ITEMS;
    save_serialize(&g, 42u, payload, sizeof payload);
    envelope(text, sizeof text, SAVE_SCHEMA, payload);
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_OK);
    game_init(&back, 1920, 1080, s.seed); save_apply(&back, &s);
    CHECK(back.finished);
}

static void test_damaged_and_incompatible(void)
{
    struct saved s;
    char text[1024], payload[512];
    struct game g;
    game_init(&g, 1920, 1080, 7u);
    save_serialize(&g, 7u, payload, sizeof payload);

    CHECK(save_parse_envelope("", 0, &s) == SAVE_NONE);
    CHECK(save_parse_envelope("this is text, not JSON", 22, &s) == SAVE_DAMAGED);
    envelope(text, sizeof text, SAVE_SCHEMA, payload);
    CHECK(save_parse_envelope(text, strlen(text) / 2, &s) == SAVE_DAMAGED);          /* truncated */
    strcat(text, "x");
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);              /* trailing garbage */

    envelope(text, sizeof text, 2, payload);
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_INCOMPATIBLE && s.schema == 2);

    snprintf(text, sizeof text, "{\"format\":\"other/1\",\"schema\":1,\"payload\":%s}", payload);
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);

    envelope(text, sizeof text, SAVE_SCHEMA, "{\"seed\":7,\"x\":1,\"y\":1,\"elapsed\":0,\"collected\":3,\"taken\":[1,0,0,0,0]}");
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);              /* count mismatch */
    envelope(text, sizeof text, SAVE_SCHEMA, "{\"seed\":7,\"x\":1,\"y\":1,\"elapsed\":0,\"collected\":1,\"taken\":[1,0,0]}");
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);              /* wrong length */
    envelope(text, sizeof text, SAVE_SCHEMA, "{\"seed\":7,\"x\":\"1\",\"y\":1,\"elapsed\":0,\"collected\":0,\"taken\":[0,0,0,0,0]}");
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);              /* wrong type */
    envelope(text, sizeof text, SAVE_SCHEMA, "{\"seed\":7,\"x\":1,\"y\":1,\"elapsed\":0,\"collected\":0,\"taken\":[2,0,0,0,0]}");
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);              /* flag out of range */

    /* Deep nesting and a huge file must be rejected without recursion trouble. */
    char deep[200];
    memset(deep, '[', 100); memset(deep + 100, ']', 99); deep[199] = 0;
    envelope(text, sizeof text, SAVE_SCHEMA, deep);
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);
    CHECK(save_parse_envelope(text, SAVE_FILE_MAX_BYTES + 1, &s) == SAVE_DAMAGED);

    /* A copy the console recovered from save.json.prev says so. */
    snprintf(text, sizeof text, "{\"format\":\"neptune-save/1\",\"schema\":1,\"recovered_from\":\"save.json.prev\",\"payload\":%s}", payload);
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_OK && s.recovered == 1);
    envelope(text, sizeof text, SAVE_SCHEMA, payload);
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_OK && s.recovered == 0);

    /* Keys may come in any order and with whitespace. */
    snprintf(text, sizeof text, "{ \"payload\" : %s ,\n \"schema\" : 1, \"format\" : \"neptune-save/1\" }", payload);
    CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_OK);

    /* A payload larger than the bound cannot be produced by save_serialize... */
    char tiny[16];
    CHECK(save_serialize(&g, 7u, tiny, sizeof tiny) == -1);
}

/* Both naming generations of the envelope (docs/game-cards.md) parse; any other format
 * string, including a near miss, is damaged. The console, not the game,
 * decides which generation a card may use. */
static void test_both_generations(void)
{
    struct game g;
    game_init(&g, 1920, 1080, 9u);
    char payload[512], text[1024];
    save_serialize(&g, 9u, payload, sizeof payload);
    struct saved s;
    const char *accepted[] = { "mun-save/1", "neptune-save/1" };
    for (int i = 0; i < 2; i++) {
        snprintf(text, sizeof text, "{\"format\":\"%s\",\"schema\":1,\"payload\":%s}", accepted[i], payload);
        CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_OK && s.seed == 9u);
    }
    const char *refused[] = { "mun-save/2", "neptune-save/2", "mun-save", "MUN-save/1", "" };
    for (int i = 0; i < 5; i++) {
        snprintf(text, sizeof text, "{\"format\":\"%s\",\"schema\":1,\"payload\":%s}", refused[i], payload);
        CHECK(save_parse_envelope(text, strlen(text), &s) == SAVE_DAMAGED);
    }
}

int main(void)
{
    test_round_trip();
    test_damaged_and_incompatible();
    test_both_generations();
    if (failures) {
        fprintf(stderr, "test_save: %d failure(s)\n", failures);
        return 1;
    }
    printf("test_save: OK\n");
    return 0;
}
