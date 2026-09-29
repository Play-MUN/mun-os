/* MUN GL Probe: a card game that stands in for a complete SDL/OpenGL/OpenAL
 * game with its data, which this repository never carries. It exercises the
 * provisional linux-arm64-gl-v0 runtime profile from inside a real game unit:
 * a KMS/DRM display through SDL2, desktop OpenGL loaded at run time the way
 * SDL games do it (SDL_GL_GetProcAddress, immediate mode), keyboard input
 * through SDL, and a tone through OpenAL. It draws no text: what it measured
 * goes to stderr (the unit's journal), what it shows is colour.
 *
 *   arrows  move the copper square          S  play a beep (OpenAL)
 *   Esc     exit 0                          F  exit 3 (a failed game)
 *   C       abort() (a crashed game)        SIGTERM ends the loop, exit 0
 *
 * Bottom bar: green when OpenAL opened a device, red when it did not. The
 * frame counter it logs is the probe's own rate for a trivial scene; it says
 * nothing about a real game's performance. */
#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <AL/al.h>
#include <AL/alc.h>
#include <SDL.h>

/* GL 1.x entry points, resolved through SDL as SDL games do; no -lGL. */
typedef unsigned int GLenum; typedef unsigned char GLboolean; typedef int GLint; typedef int GLsizei;
typedef float GLfloat; typedef double GLdouble; typedef unsigned int GLbitfield; typedef unsigned char GLubyte;
#define GL_COLOR_BUFFER_BIT 0x00004000
#define GL_TRIANGLES 0x0004
#define GL_QUADS 0x0007
#define GL_PROJECTION 0x1701
#define GL_MODELVIEW 0x1700
#define GL_VENDOR 0x1F00
#define GL_RENDERER 0x1F01
#define GL_VERSION 0x1F02
#define GL_EXTENSIONS 0x1F03

static void (*p_glClear)(GLbitfield);
static void (*p_glClearColor)(GLfloat, GLfloat, GLfloat, GLfloat);
static void (*p_glViewport)(GLint, GLint, GLsizei, GLsizei);
static void (*p_glMatrixMode)(GLenum);
static void (*p_glLoadIdentity)(void);
static void (*p_glOrtho)(GLdouble, GLdouble, GLdouble, GLdouble, GLdouble, GLdouble);
static void (*p_glBegin)(GLenum);
static void (*p_glEnd)(void);
static void (*p_glVertex2f)(GLfloat, GLfloat);
static void (*p_glColor3f)(GLfloat, GLfloat, GLfloat);
static void (*p_glRotatef)(GLfloat, GLfloat, GLfloat, GLfloat);
static void (*p_glTranslatef)(GLfloat, GLfloat, GLfloat);
static void (*p_glPushMatrix)(void);
static void (*p_glPopMatrix)(void);
static const GLubyte *(*p_glGetString)(GLenum);

static int load_gl(void)
{
#define LOAD(name) do { *(void **)&p_##name = SDL_GL_GetProcAddress(#name); \
    if (!p_##name) { fprintf(stderr, "mun-gl-probe: missing GL symbol %s\n", #name); return -1; } } while (0)
    LOAD(glClear); LOAD(glClearColor); LOAD(glViewport); LOAD(glMatrixMode); LOAD(glLoadIdentity);
    LOAD(glOrtho); LOAD(glBegin); LOAD(glEnd); LOAD(glVertex2f); LOAD(glColor3f); LOAD(glRotatef);
    LOAD(glTranslatef); LOAD(glPushMatrix); LOAD(glPopMatrix); LOAD(glGetString);
#undef LOAD
    return 0;
}

static void quad(float x, float y, float w, float h)
{
    p_glBegin(GL_QUADS);
    p_glVertex2f(x, y); p_glVertex2f(x + w, y); p_glVertex2f(x + w, y + h); p_glVertex2f(x, y + h);
    p_glEnd();
}

/* ---------------------------------------------------------------- OpenAL */
struct audio { ALCdevice *device; ALCcontext *context; ALuint buffer, source; int ok; };

static void audio_open(struct audio *a)
{
    memset(a, 0, sizeof *a);
    a->device = alcOpenDevice(NULL);
    if (!a->device) {
        fprintf(stderr, "mun-gl-probe: audio: alcOpenDevice failed (no device); continuing without sound\n");
        return;
    }
    a->context = alcCreateContext(a->device, NULL);
    if (!a->context || !alcMakeContextCurrent(a->context)) {
        fprintf(stderr, "mun-gl-probe: audio: no context; continuing without sound\n");
        return;
    }
    const ALCchar *name = alcIsExtensionPresent(a->device, "ALC_ENUMERATE_ALL_EXT")
        ? alcGetString(a->device, 0x1013 /* ALC_ALL_DEVICES_SPECIFIER */)
        : alcGetString(a->device, ALC_DEVICE_SPECIFIER);
    fprintf(stderr, "mun-gl-probe: audio: device \"%s\", AL \"%s\" renderer \"%s\"\n",
            name ? name : "?", alGetString(AL_VERSION), alGetString(AL_RENDERER));
    /* 0.25 s of a 440 Hz sine, 16-bit mono 44.1 kHz, faded at both ends. */
    enum { RATE = 44100, N = RATE / 4 };
    static int16_t pcm[N];
    for (int i = 0; i < N; i++) {
        float env = fminf(1.0f, fminf(i / 800.0f, (N - i) / 800.0f));
        pcm[i] = (int16_t)(sinf(2.0f * 3.14159265f * 440.0f * i / RATE) * 12000.0f * env);
    }
    alGenBuffers(1, &a->buffer);
    alBufferData(a->buffer, AL_FORMAT_MONO16, pcm, sizeof pcm, RATE);
    alGenSources(1, &a->source);
    alSourcei(a->source, AL_BUFFER, (ALint)a->buffer);
    a->ok = alGetError() == AL_NO_ERROR;
    if (!a->ok)
        fprintf(stderr, "mun-gl-probe: audio: AL error while preparing the tone\n");
}

static void audio_beep(struct audio *a)
{
    if (a->ok) { alSourceStop(a->source); alSourcePlay(a->source); }
}

static void audio_close(struct audio *a)
{
    if (a->ok) { alDeleteSources(1, &a->source); alDeleteBuffers(1, &a->buffer); }
    if (a->context) { alcMakeContextCurrent(NULL); alcDestroyContext(a->context); }
    if (a->device) alcCloseDevice(a->device);
}

/* ------------------------------------------------------ card content probe */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>

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

/* What a mount-access game sees of its card (docs/runtime.md): the mount options
 * of MUN_CONTENT_DIR, how many entries it holds, the first line of
 * probe.txt if present, and that writing and executing there are refused.
 * All reported, none fatal: the probe draws with or without content. */
static void content_probe(void)
{
    const char *dir = console_env("CONTENT_DIR");
    if (!dir) { fprintf(stderr, "mun-gl-probe: content: no MUN_CONTENT_DIR (copy access)\n"); return; }
    /* The mount that holds the content dir: the longest mount target that is
     * a prefix of it (the card is mounted whole; content is a directory in it). */
    FILE *mounts = fopen("/proc/self/mounts", "r");
    char line[1024], best_where[256] = "", best_type[64] = "", best_opts[512] = "";
    while (mounts && fgets(line, sizeof line, mounts)) {
        char dev[256], where[256], type[64], opts[512];
        if (sscanf(line, "%255s %255s %63s %511s", dev, where, type, opts) != 4) continue;
        size_t n = strlen(where);
        int prefix = strncmp(where, dir, n) == 0 && (dir[n] == '/' || dir[n] == 0 || strcmp(where, "/") == 0);
        if (prefix && n >= strlen(best_where)) {
            snprintf(best_where, sizeof best_where, "%s", where);
            snprintf(best_type, sizeof best_type, "%s", type);
            snprintf(best_opts, sizeof best_opts, "%s", opts);
        }
    }
    if (mounts) fclose(mounts);
    fprintf(stderr, "mun-gl-probe: content: %s lives on %s (%s) mounted %s\n", dir, best_where, best_type, best_opts);
    DIR *d = opendir(dir); int entries = 0;
    if (d) { struct dirent *e; while ((e = readdir(d))) if (e->d_name[0] != '.') entries++; closedir(d); }
    else fprintf(stderr, "mun-gl-probe: content: opendir: %s\n", strerror(errno));
    char path[1024]; snprintf(path, sizeof path, "%s/probe.txt", dir);
    FILE *f = fopen(path, "r"); char text[128] = "(no probe.txt)";
    if (f) { if (fgets(text, sizeof text, f)) text[strcspn(text, "\n")] = 0; fclose(f); }
    fprintf(stderr, "mun-gl-probe: content: %d entries, probe.txt: \"%s\"\n", entries, text);
    snprintf(path, sizeof path, "%s/probe-write.tmp", dir);
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd >= 0) { close(fd); unlink(path); fprintf(stderr, "mun-gl-probe: content: WRITE SUCCEEDED (must not)\n"); }
    else fprintf(stderr, "mun-gl-probe: content: write refused: %s\n", strerror(errno));
    snprintf(path, sizeof path, "%s/probe.sh", dir);
    if (access(path, F_OK) == 0) {
        pid_t pid = fork();
        if (pid == 0) { execl(path, path, (char *)NULL); _exit(errno == EACCES ? 126 : 125); }
        int status = 0; if (pid > 0) waitpid(pid, &status, 0);
        int code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
        fprintf(stderr, "mun-gl-probe: content: exec of probe.sh %s (child exit %d)\n",
                code == 126 ? "refused: Permission denied" : code == 0 ? "SUCCEEDED (must not)" : "failed", code);
    }
}

/* ------------------------------------------------------------------ main */
static const char *ext_flag(const char *exts, const char *name)
{
    return (exts && strstr(exts, name)) ? "yes" : "no";
}

int main(void)
{
    const char *card_id = console_env("CARD_ID");
    const char *profile = console_env("RUNTIME_PROFILE");
    SDL_SetHint(SDL_HINT_VIDEO_ALLOW_SCREENSAVER, "0");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "mun-gl-probe: SDL_Init: %s\n", SDL_GetError());
        return 2;
    }
    fprintf(stderr, "mun-gl-probe: video driver %s, card %s, profile %s\n",
            SDL_GetCurrentVideoDriver(), card_id ? card_id : "-", profile ? profile : "-");
    /* The window takes the display's own mode; without a valid mode there is
     * nothing sensible to open. MUN_PROBE_FAULT=display_mode is a lab hook
     * that makes this query fail on purpose, so the branch can be exercised. */
    SDL_DisplayMode mode = {0, 0, 0, 0, NULL};
    const char *fault = getenv("MUN_PROBE_FAULT");
    int mode_ok = SDL_GetCurrentDisplayMode(0, &mode) == 0;
    if (fault && strcmp(fault, "display_mode") == 0) mode_ok = 0;
    if (!mode_ok || mode.w <= 0 || mode.h <= 0) {
        fprintf(stderr, "mun-gl-probe: no usable display mode (%s); not opening a window\n",
                mode_ok ? "zero size" : SDL_GetError());
        SDL_Quit();
        return 2;
    }
    fprintf(stderr, "mun-gl-probe: display %dx%d @%dHz %s\n", mode.w, mode.h, mode.refresh_rate,
            SDL_GetPixelFormatName(mode.format));
    if (SDL_GL_LoadLibrary(NULL) != 0) {
        fprintf(stderr, "mun-gl-probe: SDL_GL_LoadLibrary: %s\n", SDL_GetError());
        return 2;
    }
    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);   /* compatibility profile: the default, as such games ask */
    SDL_Window *window = SDL_CreateWindow("MUN GL Probe", 0, 0, mode.w, mode.h,
                                          SDL_WINDOW_OPENGL | SDL_WINDOW_FULLSCREEN);
    if (!window) {
        fprintf(stderr, "mun-gl-probe: SDL_CreateWindow: %s\n", SDL_GetError());
        return 2;
    }
    SDL_GLContext gl = SDL_GL_CreateContext(window);
    if (!gl) {
        fprintf(stderr, "mun-gl-probe: SDL_GL_CreateContext: %s\n", SDL_GetError());
        return 2;
    }
    if (load_gl() < 0)
        return 2;
    SDL_GL_SetSwapInterval(1);
    const char *exts = (const char *)p_glGetString(GL_EXTENSIONS);
    fprintf(stderr, "mun-gl-probe: GL vendor \"%s\" renderer \"%s\" version \"%s\"\n",
            p_glGetString(GL_VENDOR), p_glGetString(GL_RENDERER), p_glGetString(GL_VERSION));
    fprintf(stderr, "mun-gl-probe: EXT_framebuffer_object=%s ARB_shader_objects=%s ARB_point_sprite=%s\n",
            ext_flag(exts, "GL_EXT_framebuffer_object"), ext_flag(exts, "GL_ARB_shader_objects"),
            ext_flag(exts, "GL_ARB_point_sprite"));
    int w, h;
    SDL_GL_GetDrawableSize(window, &w, &h);
    p_glViewport(0, 0, w, h);
    p_glMatrixMode(GL_PROJECTION); p_glLoadIdentity(); p_glOrtho(0, w, h, 0, -1, 1);
    p_glMatrixMode(GL_MODELVIEW); p_glLoadIdentity();

    content_probe();
    struct audio audio;
    audio_open(&audio);
    audio_beep(&audio);   /* one tone at start, so a silent run still shows on a capture */

    float px = w / 2.0f, py = h / 2.0f, size = h / 12.0f, speed = h / 2.0f;
    float flash = 0.0f, angle = 0.0f;
    int keyboards_seen = 0, frames = 0, running = 1, exit_code = 0;
    Uint32 started = SDL_GetTicks(), last = started, last_report = started;
    while (running) {
        Uint32 now = SDL_GetTicks();
        float dt = (now - last) / 1000.0f; if (dt > 0.1f) dt = 0.1f;
        last = now;
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_QUIT) running = 0;   /* SIGTERM/SIGINT arrive here through SDL */
            else if (ev.type == SDL_KEYDOWN && !ev.key.repeat) {
                keyboards_seen++;
                switch (ev.key.keysym.sym) {
                case SDLK_ESCAPE: running = 0; break;
                case SDLK_s: audio_beep(&audio); flash = 0.4f; break;
                case SDLK_f: fprintf(stderr, "mun-gl-probe: F pressed: exiting with code 3 on purpose\n"); exit_code = 3; running = 0; break;
                case SDLK_c: fprintf(stderr, "mun-gl-probe: C pressed: aborting on purpose\n"); fflush(stderr); abort();
                default: break;
                }
            }
        }
        const Uint8 *keys = SDL_GetKeyboardState(NULL);
        float dx = (float)(keys[SDL_SCANCODE_RIGHT] - keys[SDL_SCANCODE_LEFT]);
        float dy = (float)(keys[SDL_SCANCODE_DOWN] - keys[SDL_SCANCODE_UP]);
        px = fmaxf(size / 2, fminf(w - size / 2, px + dx * speed * dt));
        py = fmaxf(size / 2, fminf(h - size / 2, py + dy * speed * dt));
        angle += 90.0f * dt;
        if (flash > 0) flash -= dt;

        p_glClearColor(0.957f, 0.945f, 0.914f, 1.0f);   /* paper */
        p_glClear(GL_COLOR_BUFFER_BIT);
        /* gradient band: per-vertex colour through the fixed-function path */
        p_glBegin(GL_QUADS);
        p_glColor3f(0.90f, 0.88f, 0.84f); p_glVertex2f(0, 0); p_glVertex2f((float)w, 0);
        p_glColor3f(0.72f, 0.38f, 0.23f); p_glVertex2f((float)w, h * 0.12f); p_glVertex2f(0, h * 0.12f);
        p_glEnd();
        /* spinning ink triangle: proves the matrix stack */
        p_glPushMatrix();
        p_glTranslatef(w * 0.8f, h * 0.5f, 0); p_glRotatef(angle, 0, 0, 1);
        p_glColor3f(0.086f, 0.078f, 0.071f);
        p_glBegin(GL_TRIANGLES);
        p_glVertex2f(0, -size); p_glVertex2f(size * 0.87f, size * 0.5f); p_glVertex2f(-size * 0.87f, size * 0.5f);
        p_glEnd();
        p_glPopMatrix();
        /* the player's square: copper, whiter while a beep plays */
        p_glColor3f(0.72f + flash, 0.38f + flash, 0.23f + flash);
        quad(px - size / 2, py - size / 2, size, size);
        /* bottom bar: audio state */
        if (audio.ok) p_glColor3f(0.30f, 0.62f, 0.36f); else p_glColor3f(0.75f, 0.22f, 0.20f);
        quad(0, h - h * 0.03f, (float)w, h * 0.03f);
        SDL_GL_SwapWindow(window);
        frames++;
        if (now - last_report >= 5000) {
            fprintf(stderr, "mun-gl-probe: %d frames in %.1fs so far, %d key presses\n",
                    frames, (now - started) / 1000.0f, keyboards_seen);
            last_report = now;
        }
    }
    float seconds = (SDL_GetTicks() - started) / 1000.0f;
    fprintf(stderr, "mun-gl-probe: exit %d after %d frames in %.1fs (%.0f/s, vsync on), %d key presses, audio %s\n",
            exit_code, frames, seconds, seconds > 0 ? frames / seconds : 0.0f, keyboards_seen, audio.ok ? "ok" : "absent");
    audio_close(&audio);
    SDL_GL_DeleteContext(gl);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return exit_code;
}
