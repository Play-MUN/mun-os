// Host-side checks of the marker that guards the shell's decoding of a
// card's images (src/decodingmarker.h): it is written whole or reported as
// not written, with nothing left behind, so that nothing is decoded
// unguarded; and an export's folder names its insertion only when it is
// named as the card service names it. Real files in a temporary directory.
// Any C++20 compiler; no Qt. Run by `make check` (tests/Makefile here).
#include "decodingmarker.h"

#include <csignal>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include <sys/resource.h>
#include <unistd.h>

namespace {

namespace fs = std::filesystem;

int g_passed = 0;
int g_failed = 0;

void check(const std::string &what, bool ok, const std::string &detail = {})
{
    if (ok) {
        ++g_passed;
    } else {
        ++g_failed;
        std::printf("FAIL %s%s%s\n", what.c_str(), detail.empty() ? "" : ": ", detail.c_str());
    }
}

std::string contents(const fs::path &path)
{
    std::ifstream in(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

const std::string kInsertion = "0123456789abcdef";

void writing(const fs::path &runtime)
{
    std::string why;
    const fs::path marker = runtime / "shape-decoding";
    check("a marker is written whole", decodingmarker::write(marker.string(), kInsertion, &why)
                                           && contents(marker) == kInsertion && why.empty(), why);
    check("one that was there is replaced, not added to",
          decodingmarker::write(marker.string(), "fedcba9876543210", &why) && contents(marker) == "fedcba9876543210",
          why);
    fs::remove(marker);

    // Without a runtime directory there is nowhere to write it.
    why.clear();
    check("no path: not written, and the reason said", !decodingmarker::write({}, kInsertion, &why) && !why.empty());

    // A directory stands where the marker goes: it cannot be written, and
    // what stands there is left as it was.
    fs::create_directory(marker);
    why.clear();
    check("a directory in its place: not written", !decodingmarker::write(marker.string(), kInsertion, &why));
    check("the reason names the marker", why.find("shape-decoding") != std::string::npos, why);
    check("what stood there is left alone", fs::is_directory(marker));
    fs::remove(marker);

    // The runtime directory is gone (or was never made).
    why.clear();
    const fs::path nowhere = runtime / "gone" / "shape-decoding";
    check("no directory to write it in: not written",
          !decodingmarker::write(nowhere.string(), kInsertion, &why) && !why.empty() && !fs::exists(nowhere), why);

    // The file opens and the text does not reach it (this process may write
    // no byte to a file, for the length of this case): not written, and the
    // empty file it made is not left behind to be taken for a marker.
    struct rlimit before {};
    if (getrlimit(RLIMIT_FSIZE, &before) == 0) {
        struct rlimit none = before;
        none.rlim_cur = 0;
        void (*const handler)(int) = std::signal(SIGXFSZ, SIG_IGN);   // the write fails instead
        if (setrlimit(RLIMIT_FSIZE, &none) == 0) {
            why.clear();
            const bool written = decodingmarker::write(marker.string(), kInsertion, &why);
            setrlimit(RLIMIT_FSIZE, &before);
            check("a write that does not reach the file: not written", !written && !why.empty(), why);
            check("and nothing is left in its place", !fs::exists(marker));
        }
        std::signal(SIGXFSZ, handler);
        fs::remove(marker);
    }
}

void exportNames()
{
    using decodingmarker::insertionOf;
    check("an export's folder names its insertion", insertionOf(kInsertion + ".1") == kInsertion);
    check("whatever the attempt", insertionOf(kInsertion + ".123456789") == kInsertion);
    for (const std::string &name : std::vector<std::string>{std::string(), kInsertion, kInsertion + ".",
                                                     kInsertion + ".1234567890",
                                                     kInsertion + ".1a", kInsertion + "-1", "0123456789ABCDEF.1",
                                                     "0123456789abcde.1", "0123456789abcdefg.1", "scene-reef",
                                                     "../" + kInsertion + ".1", kInsertion + ".1/",
                                                     " " + kInsertion + ".1"})
        check("no insertion in '" + name + "'", insertionOf(name).empty());
}

} // namespace

int main()
{
    const fs::path runtime = fs::temp_directory_path() / ("mun-decodingmarker-" + std::to_string(getpid()));
    fs::remove_all(runtime);
    fs::create_directories(runtime);
    writing(runtime);
    exportNames();
    fs::remove_all(runtime);
    std::printf("decoding marker: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed == 0 ? 0 : 1;
}
