# Host-side checks of the shell's display and gamepad rules and of Shape
# motion's clock (src/displaydevice.*, src/gamepadmap.*, src/motionclock.h):
# any C++20 compiler, no Qt, no display, no device. The image build compiles the same files into the shell.
HOSTCXX ?= c++
CXXFLAGS = -std=c++20 -O1 -Wall -Wextra -Wpedantic -I../src
TESTS    = displaydevice_test gamepadmap_test motionclock_test

test: $(TESTS)
	./displaydevice_test
	./gamepadmap_test
	./motionclock_test

displaydevice_test: displaydevice_test.cpp ../src/displaydevice.cpp ../src/displaydevice.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ displaydevice_test.cpp ../src/displaydevice.cpp

gamepadmap_test: gamepadmap_test.cpp ../src/gamepadmap.cpp ../src/gamepadmap.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ gamepadmap_test.cpp ../src/gamepadmap.cpp

motionclock_test: motionclock_test.cpp ../src/motionclock.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ motionclock_test.cpp

clean:
	rm -f $(TESTS)

.PHONY: clean test
