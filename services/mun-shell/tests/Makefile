# Host-side checks of the shell's display rules (src/displaydevice.*): any
# C++20 compiler, no Qt, no display. The image build compiles the same file
# into the shell.
HOSTCXX ?= c++
TEST     = displaydevice_test

test: $(TEST).cpp ../src/displaydevice.cpp ../src/displaydevice.h
	$(HOSTCXX) -std=c++20 -O1 -Wall -Wextra -Wpedantic -I../src -o $(TEST) $(TEST).cpp ../src/displaydevice.cpp
	./$(TEST)

clean:
	rm -f $(TEST)

.PHONY: clean test
