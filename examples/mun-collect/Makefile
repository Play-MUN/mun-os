# MUN Collect: built inside the console guest (Debian 13, gcc 14, aarch64).
# Static so the binary on the card has no runtime library dependencies.
CC      ?= gcc
CFLAGS  ?= -O2 -std=c11 -Wall -Wextra -Wpedantic -D_GNU_SOURCE
LDFLAGS ?= -static
SRC      = main.c fb.c input.c draw.c game.c save.c
BIN      = mun-collect
# Host-side checks of the save module: any C11 compiler, no framebuffer.
HOSTCC  ?= cc

$(BIN): $(SRC) fb.h input.h draw.h game.h save.h
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS)

test: tests/test_save.c save.c game.c save.h game.h
	$(HOSTCC) -std=c11 -Wall -Wextra -Wpedantic -o tests/test_save tests/test_save.c save.c game.c -lm
	./tests/test_save

clean:
	rm -f $(BIN) tests/test_save

.PHONY: clean test
