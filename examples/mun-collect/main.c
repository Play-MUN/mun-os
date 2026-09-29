/* MUN Collect: a deliberately small test game that lives on a Game Card.
 * Move the ink square with the arrow keys, collect the five copper discs,
 * press S to save your progress on the card, Esc to return to the console.
 *
 * It draws straight to the framebuffer and reads evdev, which is what the
 * console hands over to a game (docs/runtime.md). Saving goes through the console
 * (docs/saves.md): the game never touches the card. It reads the current save
 * from the file the console left in its working directory and asks for a
 * write over the socket the console opened for this session. Both are
 * optional: without them the game simply cannot save. */
#include <ctype.h>
#include <errno.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "draw.h"
#include "fb.h"
#include "game.h"
#include "input.h"
#include "save.h"

static volatile sig_atomic_t stop_requested;

static void on_term(int sig) { (void)sig; stop_requested = 1; }

static double now_seconds(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Read the save file the console staged for us (bounded). Returns the status
 * and fills `saved` on SAVE_OK. */
static enum save_status load_save(const char *path, struct saved *saved)
{
    static char buf[SAVE_FILE_MAX_BYTES + 2];
    FILE *f = path ? fopen(path, "rb") : NULL;
    if (!f)
        return SAVE_NONE;
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    return save_parse_envelope(buf, n, saved);
}

/* Ask the console to write `payload`. One connection, one request line, one
 * reply line, with a bound on how long we wait; the frame we drew before
 * calling this says "GUARDANDO". Returns 0 on success and fills `msg`. */
static int request_save(const char *socket_path, const char *payload, char *msg, size_t msglen)
{
    if (!socket_path) {
        snprintf(msg, msglen, "SIN CONSOLA: NO SE PUEDE GUARDAR");
        return -1;
    }
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    if (fd < 0 || strlen(socket_path) >= sizeof addr.sun_path) {
        snprintf(msg, msglen, "ERROR AL GUARDAR: SOCKET");
        if (fd >= 0) close(fd);
        return -1;
    }
    strcpy(addr.sun_path, socket_path);
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        snprintf(msg, msglen, "ERROR AL GUARDAR: %s", strerror(errno));
        close(fd);
        return -1;
    }
    static char line[SAVE_MAX_BYTES + 128];
    int n = snprintf(line, sizeof line, "{\"type\":\"save\",\"schema\":%d,\"data\":%s}\n", SAVE_SCHEMA, payload);
    if (n <= 0 || (size_t)n >= sizeof line || write(fd, line, (size_t)n) != n) {
        snprintf(msg, msglen, "ERROR AL GUARDAR: ENVIO");
        close(fd);
        return -1;
    }
    /* The console answers after the card is written, synced and read-only again. */
    struct pollfd pfd = { fd, POLLIN, 0 };
    char reply[1024];
    size_t got = 0;
    double deadline = now_seconds() + 25.0;
    while (got < sizeof reply - 1) {
        int remaining = (int)((deadline - now_seconds()) * 1000);
        if (remaining <= 0 || poll(&pfd, 1, remaining) <= 0) {
            snprintf(msg, msglen, "ERROR AL GUARDAR: SIN RESPUESTA");
            close(fd);
            return -1;
        }
        ssize_t r = read(fd, reply + got, sizeof reply - 1 - got);
        if (r <= 0) break;
        got += (size_t)r;
        if (memchr(reply, '\n', got)) break;
    }
    close(fd);
    reply[got] = 0;
    if (strstr(reply, "\"ok\": true") || strstr(reply, "\"ok\":true")) {
        snprintf(msg, msglen, "GUARDADO EN LA GAME CARD");
        return 0;
    }
    const char *code = strstr(reply, "\"code\": \"");
    if (!code) code = strstr(reply, "\"code\":\"");
    if (code) {
        code = strchr(code + 7, '"') + 1;
        char short_code[48];
        size_t len = strcspn(code, "\"");
        if (len >= sizeof short_code) len = sizeof short_code - 1;
        memcpy(short_code, code, len);
        short_code[len] = 0;
        for (size_t i = 0; i < len; i++) short_code[i] = (char)toupper((unsigned char)short_code[i]);
        snprintf(msg, msglen, "ERROR AL GUARDAR: %s", short_code);
    } else {
        snprintf(msg, msglen, "ERROR AL GUARDAR");
    }
    return -1;
}

/* The console's environment under its MUN_ name, or under the NEPTUNE_ name a
 * console from before the MUN naming used (it publishes both today). */
static const char *console_env(const char *name)
{
    char key[64];
    snprintf(key, sizeof key, "MUN_%s", name);
    const char *value = getenv(key);
    if (value)
        return value;
    snprintf(key, sizeof key, "NEPTUNE_%s", name);
    return getenv(key);
}

int main(void)
{
    struct fb fb;
    struct input in;
    struct game g;
    char err[160];
    const char *card_id = console_env("CARD_ID");
    const char *fbdev = console_env("FRAMEBUFFER");
    const char *save_file = console_env("SAVE_FILE");
    const char *save_socket = console_env("SAVE_SOCKET");
    const char *frame_ms_env = getenv("MUN_FRAME_MS");   /* test hook: frame interval, default 16 */
    long frame_us = 16000;
    if (frame_ms_env && atol(frame_ms_env) > 0)
        frame_us = atol(frame_ms_env) * 1000;
    double last;
    int frames = 0;

    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);

    if (fb_open(&fb, fbdev ? fbdev : "/dev/fb0", err, sizeof err) < 0) {
        fprintf(stderr, "mun-collect: %s\n", err);
        return 2;
    }
    if (input_open(&in, err, sizeof err) < 0) {
        fprintf(stderr, "mun-collect: %s\n", err);
        fb_close(&fb);
        return 3;
    }
    fprintf(stderr, "mun-collect: %ux%u %u bpp, line %u, %d keyboard(s), card %s\n",
            fb.width, fb.height, fb.bpp, fb.line_length, in.count, card_id ? card_id : "-");

    /* Continuity comes from the card: a compatible save restores the seed (so
     * the discs are where they were), the position and what was collected. */
    struct saved saved;
    enum save_status status = load_save(save_file, &saved);
    unsigned seed = status == SAVE_OK ? saved.seed : (unsigned)time(NULL);
    game_init(&g, (float)fb.width, (float)fb.height, seed);
    if (status == SAVE_OK)
        save_apply(&g, &saved);
    fprintf(stderr, "mun-collect: save %s%s\n", save_status_text(status),
            status == SAVE_INCOMPATIBLE ? " (schema mismatch)" : "");

    char notice[96];
    snprintf(notice, sizeof notice, "%s", save_status_text(status));
    if (status == SAVE_OK)
        snprintf(notice, sizeof notice, saved.recovered ? "COPIA ANTERIOR: %d/%d" : "PARTIDA RECUPERADA: %d/%d",
                 g.collected, MUN_ITEMS);
    double notice_until = now_seconds() + 4.0;
    int save_pending = 0, s_was_down = 0;

    const uint32_t paper = fb_rgb(&fb, 0xF4, 0xF1, 0xE9), ink = fb_rgb(&fb, 0x16, 0x14, 0x12);
    const uint32_t copper = fb_rgb(&fb, 0xB8, 0x62, 0x3A), grid = fb_rgb(&fb, 0xE6, 0xE1, 0xD7);
    const uint32_t muted = fb_rgb(&fb, 0x7C, 0x73, 0x69), soft = fb_rgb(&fb, 0xEA, 0xD6, 0xC8);
    const uint32_t danger = fb_rgb(&fb, 0xC4, 0x3E, 0x2B);
    const int scale = (int)(fb.height / 180);   /* 6 at 1080p */
    const int step = (int)(fb.width / 22);

    last = now_seconds();
    while (!stop_requested) {
        double t = now_seconds();
        float dt = (float)(t - last);
        if (dt > 0.1f) dt = 0.1f;
        last = t;

        input_poll(&in);
        if (in.quit)
            break;
        int dx = (in.down[KEY_RIGHT] ? 1 : 0) - (in.down[KEY_LEFT] ? 1 : 0);
        int dy = (in.down[KEY_DOWN] ? 1 : 0) - (in.down[KEY_UP] ? 1 : 0);
        game_update(&g, dx, dy, dt);
        if (in.down[KEY_S] && !s_was_down) {
            save_pending = 1;      /* edge: one save per key press */
            snprintf(notice, sizeof notice, "GUARDANDO...");
            notice_until = t + 30.0;
        }
        s_was_down = in.down[KEY_S];

        draw_clear(&fb, paper);
        for (int x = step; x < (int)fb.width; x += step) draw_rect(&fb, x, 0, 1, (int)fb.height, grid);
        for (int y = step; y < (int)fb.height; y += step) draw_rect(&fb, 0, y, (int)fb.width, 1, grid);
        for (int i = 0; i < MUN_ITEMS; i++)
            if (!g.items[i].taken) {
                draw_disc(&fb, (int)g.items[i].x, (int)g.items[i].y, (int)(g.size * 0.45f), copper);
                draw_disc(&fb, (int)g.items[i].x - (int)(g.size * 0.1f), (int)g.items[i].y, (int)(g.size * 0.3f), soft);
            }
        draw_rect(&fb, (int)(g.px - g.size / 2), (int)(g.py - g.size / 2), (int)g.size, (int)g.size, ink);

        char counter[32];
        snprintf(counter, sizeof counter, "%d/%d", g.collected, MUN_ITEMS);
        draw_text(&fb, step / 2, step / 2, scale, "MUN COLLECT", ink);
        draw_text(&fb, (int)fb.width - step / 2 - draw_text_width(scale * 2, counter), step / 2, scale * 2, counter, copper);
        draw_text(&fb, step / 2, (int)fb.height - step / 2 - 7 * scale, scale, "S: GUARDAR   ESC: SALIR", muted);
        if (t < notice_until) {
            int w = draw_text_width(scale, notice);
            int is_error = strncmp(notice, "ERROR", 5) == 0 || strncmp(notice, "PARTIDA DANADA", 14) == 0
                           || strncmp(notice, "PARTIDA DE OTRA", 15) == 0 || strncmp(notice, "SIN CONSOLA", 11) == 0;
            draw_text(&fb, (int)fb.width - step / 2 - w, (int)fb.height - step / 2 - 7 * scale, scale, notice,
                      is_error ? danger : copper);
        }
        if (g.finished) {
            const char *msg = "COMPLETADO! ESC PARA VOLVER";
            int w = draw_text_width(scale * 2, msg);
            draw_rect(&fb, (int)fb.width / 2 - w / 2 - step / 2, (int)fb.height / 2 - 12 * scale, w + step, 24 * scale, paper);
            draw_rect(&fb, (int)fb.width / 2 - w / 2 - step / 2, (int)fb.height / 2 - 12 * scale, w + step, scale / 2, copper);
            draw_text(&fb, (int)fb.width / 2 - w / 2, (int)fb.height / 2 - 7 * scale, scale * 2, msg, ink);
        }
        fb_present(&fb);
        frames++;

        if (save_pending) {
            /* The "GUARDANDO..." frame is on screen; now block on the console.
             * Input events queue up meanwhile and are drained on the next poll. */
            save_pending = 0;
            char payload[512];
            if (save_serialize(&g, seed, payload, sizeof payload) < 0) {
                snprintf(notice, sizeof notice, "ERROR AL GUARDAR: PARTIDA");
            } else {
                int rc = request_save(save_socket, payload, notice, sizeof notice);
                fprintf(stderr, "mun-collect: save %s (%s)\n", rc == 0 ? "ok" : "failed", notice);
            }
            notice_until = now_seconds() + 4.0;
            last = now_seconds();   /* do not count the wait as game time */
        }
        usleep((useconds_t)frame_us); /* ~60 updates per second by default */
    }

    fprintf(stderr, "mun-collect: exit after %d frames, %d/%d collected, %.1fs%s\n",
            frames, g.collected, MUN_ITEMS, g.elapsed, stop_requested ? " (terminated)" : "");
    input_close(&in);
    draw_clear(&fb, paper);
    fb_present(&fb);
    fb_close(&fb);
    return 0;
}
