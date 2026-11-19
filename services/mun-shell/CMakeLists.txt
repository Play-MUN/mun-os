cmake_minimum_required(VERSION 3.25)

# MUN Shell: the console's first user-facing session. See README.md for how
# it draws: Qt Quick's software renderer on the framebuffer, or a graphics API
# where the hardware provides one.
project(mun-shell VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# Set by deploy/build.sh so the running shell can show which build it is.
set(MUN_BUILD_ID "dev" CACHE STRING "Opaque build identifier shown in the system info screen")

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Network Qml Quick)
# Shaders for the graphics path are compiled at build time (qsb) and built
# into the binary; nothing of Shader Tools is needed at run time.
find_package(Qt6 6.5 REQUIRED COMPONENTS ShaderTools)
# The interface sounds go straight to ALSA (src/systemsounds.h), the library
# the games' profile already brings into the image.
find_package(PkgConfig REQUIRED)
pkg_check_modules(ALSA REQUIRED IMPORTED_TARGET alsa)
# The display's modes and the mode actually set are read from DRM directly
# (src/displaymode.cpp); Qt's KMS support is not public API.
pkg_check_modules(DRM REQUIRED IMPORTED_TARGET libdrm)
find_package(Threads REQUIRED)
qt_standard_project_setup(REQUIRES 6.5)

qt_add_executable(mun-shell
    src/main.cpp
    src/displaymode.cpp
    src/displaymode.h
    src/displaydevice.cpp
    src/displaydevice.h
    src/systeminfo.cpp
    src/systeminfo.h
    src/powercontrol.cpp
    src/powercontrol.h
    src/cardclient.cpp
    src/cardclient.h
    src/launchclient.cpp
    src/launchclient.h
    src/shellsettings.cpp
    src/shellsettings.h
    src/systemsounds.cpp
    src/systemsounds.h
    src/playbackhealth.h
    src/gamepadmap.cpp
    src/gamepadmap.h
    src/gamepads.cpp
    src/gamepads.h
    src/orbitalbackdrop.cpp
    src/orbitalbackdrop.h
    src/orbitalcard.cpp
    src/orbitalcard.h
    src/blur.cpp
    src/blur.h
    src/bootintro.cpp
    src/bootintro.h
    src/bootintroshapes.h
    src/bootclock.cpp
    src/bootclock.h
    src/startupclock.h
    src/hairline.cpp
    src/hairline.h
    src/cssbox.cpp
    src/cssbox.h
    src/surfacenode.cpp
    src/surfacenode.h
    src/sceneclock.cpp
    src/sceneclock.h
    src/backdrop.cpp
    src/backdrop.h
    src/networknode.cpp
    src/networknode.h
    src/heroicon.cpp
    src/heroicon.h
    src/projectedview.cpp
    src/projectedview.h
    src/contrast.h
    src/readpalette.cpp
    src/readpalette.h
    src/shape.cpp
    src/shape.h
    src/shapefront.h
    src/shapeworld.cpp
    src/shapeworld.h
    src/motionscene.cpp
    src/motionscene.h
    src/motionclock.h
    src/framewatch.cpp
    src/framewatch.h
    src/renderinfo.cpp
    src/renderinfo.h
    src/memory.cpp
    src/memory.h
)

# The generated QML type registration includes headers by basename.
target_include_directories(mun-shell PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

target_compile_definitions(mun-shell PRIVATE
    MUN_SHELL_VERSION="${PROJECT_VERSION}"
    MUN_BUILD_ID="${MUN_BUILD_ID}"
)

# QML lives in the binary (qrc) so a deployment is one file plus fonts. During
# UI iteration MUN_SHELL_QML_DIR can point at a source checkout instead.
set(MUN_QML_FILES
    qml/Main.qml
    qml/Theme.qml
    qml/I18n.qml
    qml/Logo.js
    qml/Panels.qml
    qml/UiText.qml
    qml/MarkText.qml
    qml/Shadow.qml
    qml/ProjectedLayer.qml
    qml/ArcNode.qml
    qml/ArcMenu.qml
    qml/OrbitalNode.qml
    qml/OrbitalMenu.qml
    qml/OrbitalIcon.qml
    qml/OrbitalFocus.qml
    qml/OptionRow.qml
    qml/Material.qml
    qml/DetailPanel.qml
    qml/StatusBar.qml
    qml/MoonPhase.qml
    qml/ModalLayer.qml
    qml/BootLayer.qml
)
# Files under qml/ must appear at the module root to become module types;
# fonts/ keeps its directory (the binary loads them from
# :/qt/qml/MUN/Shell/fonts/).
foreach(qml_file IN LISTS MUN_QML_FILES)
    get_filename_component(qml_alias "${qml_file}" NAME)
    set_source_files_properties("${qml_file}" PROPERTIES QT_RESOURCE_ALIAS "${qml_alias}")
endforeach()
set_source_files_properties(qml/Theme.qml qml/I18n.qml PROPERTIES QT_QML_SINGLETON_TYPE TRUE)
qt_add_qml_module(mun-shell
    URI MUN.Shell
    VERSION 1.0
    # The module's types derive from QtQuick's (QQuickItem): declared, so that
    # qmllint and the QML compiler resolve them instead of reporting every
    # property of an item as unknown.
    DEPENDENCIES QtQuick
    QML_FILES ${MUN_QML_FILES}
    RESOURCES
        fonts/Archivo-Variable.ttf
        fonts/Michroma-Regular.ttf
        sounds/move.wav
        sounds/enter.wav
        sounds/back.wav
        sounds/launch.wav
        sounds/game_return.wav
        sounds/shape_exit.wav
        sounds/eject.wav
        sounds/shutdown.wav
        sounds/success.wav
        sounds/warning.wav
        sounds/error.wav
        sounds/controller_connect.wav
        sounds/controller_disconnect.wav
        sounds/startup.wav
        images/boot-silhouette.png
        images/boot-engraving.png
)

# The graphics path's own drawing (src/networknode.h): Vulkan-style GLSL,
# compiled for every API Qt Quick may use.
qt_add_shaders(mun-shell "mun-shell-shaders"
    PREFIX "/"
    FILES
        shaders/network.vert
        shaders/network.frag
        shaders/box.vert
        shaders/box.frag
        shaders/shadow.vert
        shaders/shadow.frag
        shaders/eclipse.vert
        shaders/eclipse.frag
        shaders/wave.vert
        shaders/wave.frag
        shaders/sprite.vert
        shaders/sprite.frag
)

target_link_libraries(mun-shell PRIVATE Qt6::Core Qt6::Gui Qt6::Network Qt6::Qml Qt6::Quick PkgConfig::ALSA PkgConfig::DRM Threads::Threads)

# Hardening, on Linux where the console runs: the stack protector, fortified
# libc calls, stack clash protection, control-flow protection (BTI and PAC on
# AArch64, the console's architecture), full RELRO and a stack that is not
# executable. Debian's compiler adds none of these by itself but PIE. Each
# flag is tried first, so a toolchain without one still builds;
# tests/ci/hardening_check.py then requires every property in the built
# executable, where a flag that never reached the binary shows. On x86-64,
# built only for checks, -fcf-protection marks the shell's own code, but the
# executable stays unmarked: Debian 13's x86-64 startup files carry no CET
# marking, and the linker marks only what every object does.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    include(CheckCXXCompilerFlag)
    include(CheckLinkerFlag)
    set(mun_hardening_compile -fstack-protector-strong -fstack-clash-protection)
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
        list(APPEND mun_hardening_compile -mbranch-protection=standard)
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$")
        list(APPEND mun_hardening_compile -fcf-protection=full)
    endif()
    foreach(flag IN LISTS mun_hardening_compile)
        string(MAKE_C_IDENTIFIER "mun_cxx${flag}" supported)
        check_cxx_compiler_flag(${flag} ${supported})
        if(${supported})
            target_compile_options(mun-shell PRIVATE ${flag})
        endif()
    endforeach()
    # _FORTIFY_SOURCE needs optimization, so only optimized builds get it
    # (level 3: GCC 12, Clang 16); -U first in case the compiler defines it.
    target_compile_options(mun-shell PRIVATE
        "$<$<CONFIG:Release,RelWithDebInfo,MinSizeRel>:-U_FORTIFY_SOURCE;-D_FORTIFY_SOURCE=3>")
    foreach(flag LINKER:-z,relro LINKER:-z,now LINKER:-z,noexecstack)
        string(MAKE_C_IDENTIFIER "mun_ld${flag}" supported)
        check_linker_flag(CXX ${flag} ${supported})
        if(${supported})
            target_link_options(mun-shell PRIVATE ${flag})
        endif()
    endforeach()
endif()

include(GNUInstallDirs)
install(TARGETS mun-shell RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
