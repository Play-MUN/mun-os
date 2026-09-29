#include "input.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define BITS_PER_LONG (8 * sizeof(long))
#define NBITS(x) (((x) + BITS_PER_LONG - 1) / BITS_PER_LONG)
#define TEST_BIT(bit, arr) ((arr)[(bit) / BITS_PER_LONG] & (1UL << ((bit) % BITS_PER_LONG)))

static int has_our_keys(int fd)
{
    unsigned long ev[NBITS(EV_MAX)] = {0};
    unsigned long keys[NBITS(KEY_MAX)] = {0};
    if (ioctl(fd, EVIOCGBIT(0, sizeof ev), ev) < 0 || !TEST_BIT(EV_KEY, ev))
        return 0;
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof keys), keys) < 0)
        return 0;
    return TEST_BIT(KEY_LEFT, keys) && TEST_BIT(KEY_RIGHT, keys) && TEST_BIT(KEY_UP, keys)
        && TEST_BIT(KEY_DOWN, keys) && TEST_BIT(KEY_ESC, keys);
}

int input_open(struct input *in, char *err, size_t errlen)
{
    DIR *dir = opendir("/dev/input");
    struct dirent *entry;
    memset(in, 0, sizeof *in);
    if (!dir) {
        snprintf(err, errlen, "cannot list /dev/input");
        return -1;
    }
    while ((entry = readdir(dir)) && in->count < MUN_MAX_KEYBOARDS) {
        char path[280];
        int fd;
        if (strncmp(entry->d_name, "event", 5) != 0)
            continue;
        snprintf(path, sizeof path, "/dev/input/%s", entry->d_name);
        fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0)
            continue;
        if (has_our_keys(fd))
            in->fds[in->count++] = fd;
        else
            close(fd);
    }
    closedir(dir);
    if (in->count == 0) {
        snprintf(err, errlen, "no keyboard with arrow keys and Esc found in /dev/input");
        return -1;
    }
    return 0;
}

void input_poll(struct input *in)
{
    struct input_event ev[32];
    for (int i = 0; i < in->count; i++) {
        for (;;) {
            ssize_t n = read(in->fds[i], ev, sizeof ev);
            if (n <= 0)
                break;
            for (size_t k = 0; k < (size_t)n / sizeof ev[0]; k++) {
                if (ev[k].type != EV_KEY || ev[k].code >= 256)
                    continue;
                if (ev[k].value == 2)
                    continue; /* autorepeat */
                in->down[ev[k].code] = ev[k].value ? 1 : 0;
                if (ev[k].code == KEY_ESC && ev[k].value)
                    in->quit = 1;
            }
        }
    }
}

void input_close(struct input *in)
{
    for (int i = 0; i < in->count; i++)
        close(in->fds[i]);
    in->count = 0;
}
