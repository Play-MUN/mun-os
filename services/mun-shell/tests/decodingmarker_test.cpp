// Host-side checks of the marker that guards the shell's decoding of a
// card's images (src/decodingmarker.h): it is written whole or reported as
// not written, with nothing left behind, so that nothing is decoded
// unguarded; an export's folder names its insertion only when it is named
// as the card service names it; and a start that finds a marker skips its
// insertion and keeps it skipped over the starts that follow. Real files in
// a temporary directory.
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

void put(const fs::path &path, const std::string &text)
{
    std::ofstream out(path, std::ios::binary);
    out << text;
}

void resuming(const fs::path &runtime)
{
    using decodingmarker::resume;
    const fs::path marker = runtime / "shape-decoding", kept = runtime / "shape-skipped";
    const std::string other = "fedcba9876543210";

    decodingmarker::Skip skip = resume(marker.string(), kept.string());
    check("no marker and nothing kept: no insertion is skipped", skip.insertion.empty() && !skip.found);
    check("and nothing is written", !fs::exists(marker) && !fs::exists(kept));

    // The last start ended while it decoded an insertion's images.
    put(marker, kInsertion);
    skip = resume(marker.string(), kept.string());
    check("a marker names the insertion to skip", skip.insertion == kInsertion && skip.found);
    check("the marker is consumed", !fs::exists(marker));
    check("the insertion is kept", contents(kept) == kInsertion);

    // The starts after it, a clean stop between them (which removes the
    // markers, never what is kept): the insertion stays skipped.
    for (int start = 0; start < 3; ++start) {
        skip = resume(marker.string(), kept.string());
        check("a later start still skips it, and does not take it for a new crash",
              skip.insertion == kInsertion && !skip.found && contents(kept) == kInsertion);
    }

    // Another insertion's decoding ends a start: it is the one skipped now.
    put(marker, other + "\n");
    skip = resume(marker.string(), kept.string());
    check("a new marker replaces what was kept", skip.insertion == other && skip.found && contents(kept) == other
                                                     && !fs::exists(marker));

    // A marker that names nothing (the shell ended as it made it).
    put(marker, "");
    skip = resume(marker.string(), kept.string());
    check("an empty marker is consumed and changes nothing", skip.insertion == other && !skip.found
                                                                 && !fs::exists(marker) && contents(kept) == other);

    // Something that is not a file where the marker goes is not a marker:
    // it is left as it is (it keeps the marker from being written, and the
    // images from being decoded).
    fs::create_directory(marker);
    skip = resume(marker.string(), kept.string());
    check("a directory in the marker's place is left alone", fs::is_directory(marker) && skip.insertion == other
                                                                 && !skip.found);
    fs::remove(marker);

    // What is kept cannot be written: this start skips the insertion all the same.
    fs::remove(kept);
    fs::create_directory(kept);
    put(marker, kInsertion);
    skip = resume(marker.string(), kept.string());
    check("an insertion that cannot be kept is skipped at this start", skip.insertion == kInsertion && skip.found
                                                                           && !fs::exists(marker));
    skip = resume(marker.string(), kept.string());
    check("and nothing is skipped after it", skip.insertion.empty() && !skip.found);
    fs::remove(kept);

    // Only an insertion's worth is read, without the white space round it.
    put(marker, "  " + kInsertion + " \n" + std::string(4096, 'x'));
    skip = resume(marker.string(), kept.string());
    check("a marker is read as far as an insertion goes", skip.insertion.size() <= 64
                                                              && skip.insertion.rfind(kInsertion, 0) == 0);
    fs::remove(kept);

    skip = resume({}, {});
    check("without a runtime directory nothing is skipped", skip.insertion.empty() && !skip.found);
}

} // namespace

void sharing(const fs::path &runtime)
{
    // The card's window and cover, and its world, are read by two threads
    // that can overlap: the marker stays until the last of them is done.
    std::string why;
    const fs::path marker = runtime / "shape-decoding";
    check("a first reader holds the marker", decodingmarker::hold(marker.string(), kInsertion, &why)
                                                 && contents(marker) == kInsertion, why);
    check("a second reader of the same insertion holds it too", decodingmarker::hold(marker.string(), kInsertion, &why),
          why);
    decodingmarker::release(marker.string());
    check("the first one done leaves it while the other reads", fs::exists(marker) && contents(marker) == kInsertion);
    decodingmarker::release(marker.string());
    check("the last one done removes it", !fs::exists(marker));
    decodingmarker::release(marker.string());
    check("a release with no holder removes nothing it should not", !fs::exists(marker));
    check("and the marker can be held again", decodingmarker::hold(marker.string(), kInsertion, &why), why);
    decodingmarker::release(marker.string());
    check("a reader that could not write it holds nothing",
          !decodingmarker::hold((runtime / "absent" / "shape-decoding").string(), kInsertion, &why));
}

int main()
{
    const fs::path runtime = fs::temp_directory_path() / ("mun-decodingmarker-" + std::to_string(getpid()));
    fs::remove_all(runtime);
    fs::create_directories(runtime);
    writing(runtime);
    exportNames();
    resuming(runtime);
    sharing(runtime);
    fs::remove_all(runtime);
    std::printf("decoding marker: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed == 0 ? 0 : 1;
}
