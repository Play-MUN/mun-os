#include "save.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- a strict, bounded JSON scanner ------------------------------------- */

struct scan {
    const char *p;
    const char *end;
    int depth;
};

#define MAX_DEPTH 16

static void ws(struct scan *s)
{
    while (s->p < s->end && (*s->p == ' ' || *s->p == '\t' || *s->p == '\n' || *s->p == '\r'))
        s->p++;
}

static int skip_string(struct scan *s)
{
    if (s->p >= s->end || *s->p != '"')
        return -1;
    s->p++;
    while (s->p < s->end) {
        unsigned char c = (unsigned char)*s->p;
        if (c == '"') { s->p++; return 0; }
        if (c == '\\') {
            s->p++;
            if (s->p >= s->end) return -1;
            if (*s->p == 'u') {
                for (int i = 0; i < 4; i++) {
                    s->p++;
                    if (s->p >= s->end || !strchr("0123456789abcdefABCDEF", *s->p)) return -1;
                }
            }
        } else if (c < 0x20) {
            return -1;            /* control characters are not allowed raw */
        }
        s->p++;
    }
    return -1;
}

static int skip_number(struct scan *s)
{
    const char *start = s->p;
    if (s->p < s->end && *s->p == '-') s->p++;
    if (s->p >= s->end || *s->p < '0' || *s->p > '9') return -1;
    while (s->p < s->end && *s->p >= '0' && *s->p <= '9') s->p++;
    if (s->p < s->end && *s->p == '.') {
        s->p++;
        if (s->p >= s->end || *s->p < '0' || *s->p > '9') return -1;
        while (s->p < s->end && *s->p >= '0' && *s->p <= '9') s->p++;
    }
    if (s->p < s->end && (*s->p == 'e' || *s->p == 'E')) {
        s->p++;
        if (s->p < s->end && (*s->p == '+' || *s->p == '-')) s->p++;
        if (s->p >= s->end || *s->p < '0' || *s->p > '9') return -1;
        while (s->p < s->end && *s->p >= '0' && *s->p <= '9') s->p++;
    }
    return s->p > start ? 0 : -1;
}

static int skip_value(struct scan *s);

static int skip_container(struct scan *s, char open, char close)
{
    if (s->p >= s->end || *s->p != open) return -1;
    if (++s->depth > MAX_DEPTH) return -1;
    s->p++;
    ws(s);
    if (s->p < s->end && *s->p == close) { s->p++; s->depth--; return 0; }
    for (;;) {
        ws(s);
        if (open == '{') {
            if (skip_string(s) < 0) return -1;
            ws(s);
            if (s->p >= s->end || *s->p != ':') return -1;
            s->p++;
        }
        if (skip_value(s) < 0) return -1;
        ws(s);
        if (s->p >= s->end) return -1;
        if (*s->p == ',') { s->p++; continue; }
        if (*s->p == close) { s->p++; s->depth--; return 0; }
        return -1;
    }
}

static int skip_literal(struct scan *s, const char *word)
{
    size_t n = strlen(word);
    if ((size_t)(s->end - s->p) < n || memcmp(s->p, word, n) != 0) return -1;
    s->p += n;
    return 0;
}

static int skip_value(struct scan *s)
{
    ws(s);
    if (s->p >= s->end) return -1;
    switch (*s->p) {
    case '"': return skip_string(s);
    case '{': return skip_container(s, '{', '}');
    case '[': return skip_container(s, '[', ']');
    case 't': return skip_literal(s, "true");
    case 'f': return skip_literal(s, "false");
    case 'n': return skip_literal(s, "null");
    default:  return skip_number(s);
    }
}

/* Find `key` in the object starting at `obj` (pointing at '{'). On success
 * returns the value start and sets *vend to one past the value. Keys are
 * compared byte for byte, so only plain ASCII keys are found (ours are). */
static const char *object_get(const char *obj, const char *end, const char *key, const char **vend, int depth)
{
    struct scan s = { obj, end, depth };
    size_t klen = strlen(key);
    ws(&s);
    if (s.p >= s.end || *s.p != '{') return NULL;
    s.p++;
    ws(&s);
    if (s.p < s.end && *s.p == '}') return NULL;
    for (;;) {
        ws(&s);
        const char *kstart = s.p;
        if (skip_string(&s) < 0) return NULL;
        int match = (size_t)(s.p - kstart) == klen + 2 && memcmp(kstart + 1, key, klen) == 0;
        ws(&s);
        if (s.p >= s.end || *s.p != ':') return NULL;
        s.p++;
        ws(&s);
        const char *vstart = s.p;
        if (skip_value(&s) < 0) return NULL;
        if (match) { *vend = s.p; return vstart; }
        ws(&s);
        if (s.p >= s.end) return NULL;
        if (*s.p == ',') { s.p++; continue; }
        return NULL;
    }
}

static int number_of(const char *v, const char *vend, double *out)
{
    char buf[64];
    size_t n = (size_t)(vend - v);
    if (n == 0 || n >= sizeof buf) return -1;
    struct scan s = { v, vend, 0 };
    if (skip_number(&s) < 0 || s.p != vend) return -1;
    memcpy(buf, v, n);
    buf[n] = 0;
    *out = strtod(buf, NULL);
    return isfinite(*out) ? 0 : -1;
}

static int string_equals(const char *v, const char *vend, const char *expected)
{
    size_t n = strlen(expected);
    return (size_t)(vend - v) == n + 2 && v[0] == '"' && vend[-1] == '"' && memcmp(v + 1, expected, n) == 0;
}

/* Fill up to `cap` ints from a JSON array of integers; returns the count or -1. */
static int int_array(const char *v, const char *vend, int *out, int cap)
{
    struct scan s = { v, vend, 0 };
    ws(&s);
    if (s.p >= s.end || *s.p != '[') return -1;
    s.p++;
    ws(&s);
    int count = 0;
    if (s.p < s.end && *s.p == ']') return 0;
    for (;;) {
        ws(&s);
        const char *start = s.p;
        if (skip_number(&s) < 0) return -1;
        double value;
        if (count >= cap || number_of(start, s.p, &value) < 0 || value != (int)value) return -1;
        out[count++] = (int)value;
        ws(&s);
        if (s.p >= s.end) return -1;
        if (*s.p == ',') { s.p++; continue; }
        if (*s.p == ']') { s.p++; ws(&s); return s.p == s.end ? count : -1; }
        return -1;
    }
}

/* ---- the save payload ---------------------------------------------------- */

int save_serialize(const struct game *g, unsigned seed, char *out, size_t cap)
{
    char taken[MUN_ITEMS * 2 + 2];
    size_t n = 0;
    taken[n++] = '[';
    for (int i = 0; i < MUN_ITEMS; i++) {
        if (i) taken[n++] = ',';
        taken[n++] = g->items[i].taken ? '1' : '0';
    }
    taken[n++] = ']';
    taken[n] = 0;
    /* Static binary, no setlocale(): "%f" always uses '.' as the decimal point. */
    int written = snprintf(out, cap, "{\"seed\":%u,\"x\":%.3f,\"y\":%.3f,\"elapsed\":%.2f,\"collected\":%d,\"taken\":%s}",
                           seed, (double)g->px, (double)g->py, (double)g->elapsed, g->collected, taken);
    return (written < 0 || (size_t)written >= cap) ? -1 : written;
}

enum save_status save_parse_envelope(const char *text, size_t len, struct saved *out)
{
    if (len == 0) return SAVE_NONE;
    if (len > SAVE_FILE_MAX_BYTES) return SAVE_DAMAGED;
    const char *end = text + len;
    struct scan whole = { text, end, 0 };
    if (skip_value(&whole) < 0) return SAVE_DAMAGED;
    ws(&whole);
    if (whole.p != end) return SAVE_DAMAGED;        /* trailing garbage */

    const char *v, *vend;
    /* Either naming generation (docs/game-cards.md): mun-save/1 on cards with mun.toml,
     * neptune-save/1 on earlier ones. Which one a card may use is the
     * console's check, not the game's. */
    if (!(v = object_get(text, end, "format", &vend, 0))
        || !(string_equals(v, vend, "mun-save/1") || string_equals(v, vend, "neptune-save/1")))
        return SAVE_DAMAGED;
    double schema;
    if (!(v = object_get(text, end, "schema", &vend, 0)) || number_of(v, vend, &schema) < 0 || schema != (int)schema)
        return SAVE_DAMAGED;
    if ((int)schema != SAVE_SCHEMA) {
        out->schema = (int)schema;
        return SAVE_INCOMPATIBLE;
    }
    const char *payload, *pend;
    if (!(payload = object_get(text, end, "payload", &pend, 0)))
        return SAVE_DAMAGED;

    struct saved s;
    memset(&s, 0, sizeof s);
    s.schema = SAVE_SCHEMA;
    double num;
    if (!(v = object_get(payload, pend, "seed", &vend, 1)) || number_of(v, vend, &num) < 0 || num < 0 || num > 4294967295.0 || num != (unsigned)num)
        return SAVE_DAMAGED;
    s.seed = (unsigned)num;
    if (!(v = object_get(payload, pend, "x", &vend, 1)) || number_of(v, vend, &num) < 0) return SAVE_DAMAGED;
    s.x = (float)num;
    if (!(v = object_get(payload, pend, "y", &vend, 1)) || number_of(v, vend, &num) < 0) return SAVE_DAMAGED;
    s.y = (float)num;
    if (!(v = object_get(payload, pend, "elapsed", &vend, 1)) || number_of(v, vend, &num) < 0 || num < 0) return SAVE_DAMAGED;
    s.elapsed = (float)num;
    if (!(v = object_get(payload, pend, "collected", &vend, 1)) || number_of(v, vend, &num) < 0 || num != (int)num) return SAVE_DAMAGED;
    s.collected = (int)num;
    if (!(v = object_get(payload, pend, "taken", &vend, 1)) || int_array(v, vend, s.taken, MUN_ITEMS) != MUN_ITEMS)
        return SAVE_DAMAGED;
    int count = 0;
    for (int i = 0; i < MUN_ITEMS; i++) {
        if (s.taken[i] != 0 && s.taken[i] != 1) return SAVE_DAMAGED;
        count += s.taken[i];
    }
    if (count != s.collected) return SAVE_DAMAGED;   /* internally inconsistent: do not trust it */
    s.recovered = (v = object_get(text, end, "recovered_from", &vend, 0)) != NULL && string_equals(v, vend, "save.json.prev");
    *out = s;
    return SAVE_OK;
}

void save_apply(struct game *g, const struct saved *saved)
{
    float half = g->size * 0.5f;
    g->px = saved->x;
    g->py = saved->y;
    if (g->px < half) g->px = half;
    if (g->py < half) g->py = half;
    if (g->px > g->width - half) g->px = g->width - half;
    if (g->py > g->height - half) g->py = g->height - half;
    g->elapsed = saved->elapsed;
    g->collected = 0;
    for (int i = 0; i < MUN_ITEMS; i++) {
        g->items[i].taken = saved->taken[i];
        g->collected += saved->taken[i];
    }
    g->finished = g->collected == MUN_ITEMS;
}

const char *save_status_text(enum save_status status)
{
    switch (status) {
    case SAVE_OK:           return "PARTIDA RECUPERADA";
    case SAVE_NONE:         return "NUEVA PARTIDA";
    case SAVE_DAMAGED:      return "PARTIDA DANADA: NO SE SOBRESCRIBE SOLA";
    case SAVE_INCOMPATIBLE: return "PARTIDA DE OTRA VERSION: NO SE SOBRESCRIBE SOLA";
    }
    return "";
}
