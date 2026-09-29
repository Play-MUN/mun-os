# MUN GL Probe: built inside the console guest (Debian 13, gcc 14, aarch64).
# Dynamically linked against the system SDL2 and OpenAL Soft on purpose: the
# provisional linux-arm64-gl-v0 profile promises those libraries, and the
# launcher installer records their versions. OpenGL is not linked at all; the
# probe resolves it through SDL at run time, as SDL games do.
CC      ?= gcc
CFLAGS  ?= -O2 -std=c11 -Wall -Wextra -D_GNU_SOURCE $(shell sdl2-config --cflags)
LDLIBS  ?= $(shell sdl2-config --libs) -lopenal -lm
BIN      = mun-gl-probe

$(BIN): main.c
	$(CC) $(CFLAGS) -o $@ main.c $(LDLIBS)

clean:
	rm -f $(BIN)

.PHONY: clean
