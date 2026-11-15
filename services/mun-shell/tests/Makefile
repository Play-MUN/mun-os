# Host-side checks of the shell's display and gamepad rules, of Shape
# motion's clock, of the start-up's clock, of the sound device's health, of
# the scan of a PNG's chunks and of the marker that guards decoding
# (src/displaydevice.*, src/gamepadmap.*, src/motionclock.h,
# src/startupclock.h, src/playbackhealth.h, src/pngchunks.h,
# src/decodingmarker.h):
# any C++20 compiler, no Qt, no display, no device. The image build compiles the same files into the shell.
# And of the logic the interface keeps in its QML files (qml_test.js), where
# node is installed: the files' own functions, run against stand-ins.
HOSTCXX ?= c++
CXXFLAGS = -std=c++20 -O1 -Wall -Wextra -Wpedantic -I../src
TESTS    = displaydevice_test gamepadmap_test motionclock_test startupclock_test playbackhealth_test \
           pngchunks_test decodingmarker_test

test: $(TESTS)
	./displaydevice_test
	./gamepadmap_test
	./motionclock_test
	./startupclock_test
	./playbackhealth_test
	./pngchunks_test
	./decodingmarker_test
	@if command -v node >/dev/null 2>&1; then node qml_test.js; else echo "no node: MUN Shell interface logic tests skipped"; fi

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

pngchunks_test: pngchunks_test.cpp ../src/pngchunks.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ pngchunks_test.cpp

decodingmarker_test: decodingmarker_test.cpp ../src/decodingmarker.h
	$(HOSTCXX) $(CXXFLAGS) -o $@ decodingmarker_test.cpp

clean:
	rm -f $(TESTS)

.PHONY: clean test
