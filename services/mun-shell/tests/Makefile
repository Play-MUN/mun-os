# Host-side checks of the shell's display and gamepad rules, of Shape
# motion's clock, of the start-up's clock and of the sound device's health
# (src/displaydevice.*, src/gamepadmap.*, src/motionclock.h,
# src/startupclock.h, src/playbackhealth.h):
# any C++20 compiler, no Qt, no display, no device. The image build compiles the same files into the shell.
HOSTCXX ?= c++
CXXFLAGS = -std=c++20 -O1 -Wall -Wextra -Wpedantic -I../src
TESTS    = displaydevice_test gamepadmap_test motionclock_test startupclock_test playbackhealth_test

test: $(TESTS)
	./displaydevice_test
	./gamepadmap_test
	./motionclock_test
	./startupclock_test
	./playbackhealth_test

displaydevice_test: displaydevice_test.cpp ../src/displaydevice.cpp ../src/displaydevice.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ displaydevice_test.cpp ../src/displaydevice.cpp

gamepadmap_test: gamepadmap_test.cpp ../src/gamepadmap.cpp ../src/gamepadmap.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ gamepadmap_test.cpp ../src/gamepadmap.cpp

motionclock_test: motionclock_test.cpp ../src/motionclock.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ motionclock_test.cpp

startupclock_test: startupclock_test.cpp ../src/startupclock.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ startupclock_test.cpp

playbackhealth_test: playbackhealth_test.cpp ../src/playbackhealth.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ playbackhealth_test.cpp

clean:
	rm -f $(TESTS)

.PHONY: clean test
