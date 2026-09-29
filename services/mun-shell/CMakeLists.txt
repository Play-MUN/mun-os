cmake_minimum_required(VERSION 3.25)

# MUN Shell: the console's first user-facing session. See README.md for why
# this is Qt Quick with software rendering on the framebuffer.
project(mun-shell VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# Set by deploy/build.sh so the running shell can show which build it is.
set(MUN_BUILD_ID "dev" CACHE STRING "Opaque build identifier shown in the system info screen")

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Network Qml Quick)
qt_standard_project_setup(REQUIRES 6.5)

qt_add_executable(mun-shell
    src/main.cpp
    src/displaymode.cpp
    src/displaymode.h
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
    src/blur.cpp
    src/blur.h
    src/hairline.cpp
    src/hairline.h
    src/cssbox.cpp
    src/cssbox.h
    src/sceneclock.cpp
    src/sceneclock.h
    src/backdrop.cpp
    src/backdrop.h
    src/heroicon.cpp
    src/heroicon.h
    src/projectedview.cpp
    src/projectedview.h
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
    qml/OptionRow.qml
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
    QML_FILES ${MUN_QML_FILES}
    RESOURCES
        fonts/Archivo-Variable.ttf
        fonts/Michroma-Regular.ttf
)

target_link_libraries(mun-shell PRIVATE Qt6::Core Qt6::Gui Qt6::Network Qt6::Qml Qt6::Quick)

include(GNUInstallDirs)
install(TARGETS mun-shell RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
