/* Keyboard input for MUN Collect: finds every evdev device that advertises the
 * keys we use (arrows and Esc) by capability, never by a fixed event number,
 * and keeps a pressed/released table. */
#ifndef MUN_INPUT_H
#define MUN_INPUT_H

#include <stddef.h>

#define MUN_MAX_KEYBOARDS 8

struct input {
    int fds[MUN_MAX_KEYBOARDS];
    int count;
    unsigned char down[256]; /* indexed by KEY_* code < 256 */
    int quit;                /* Esc pressed */
};

int input_open(struct input *in, char *err, size_t errlen);
void input_poll(struct input *in);
void input_close(struct input *in);

#endif
