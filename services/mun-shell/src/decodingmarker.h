// decodingmarker is the file side of the shell's guard against an image
// whose decoding takes the shell down. Before an insertion's images are
// read, a marker naming the insertion is written in the runtime directory
// (which outlives the shell's restarts and not a reboot), and removed once
// they are read. A start that finds a marker knows which insertion it was
// reading when it ended, and skips it (Shape): the same image is not read
// again at every restart. A clean stop removes the markers itself
// (ExecStopPost in deploy/mun-shell.service). The insertion a start skips is
// kept in a file of its own, which a clean stop leaves: the launcher stops
// the shell around every game, and the insertion must stay skipped then.
//
// The guard holds only if the marker is really there before the first byte
// is decoded, so writing it says whether it is: write() is false unless the
// whole text reached the file, and the caller then decodes nothing.
//
// No Qt: checked on the host (tests/decodingmarker_test.cpp).
#pragma once

#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>

namespace decodingmarker {

// Writes the marker at `path` naming `insertion`, whole. False, with the
// reason in *why, if it is not there afterwards; what was written of it is
// then removed.
inline bool write(const std::string &path, const std::string &insertion, std::string *why)
{
    if (path.empty()) {
        *why = "there is no runtime directory";
        return false;
    }
    std::FILE *file = std::fopen(path.c_str(), "wb");
    if (!file) {
        *why = path + ": " + std::strerror(errno);
        return false;
    }
    int error = 0;
    if (std::fwrite(insertion.data(), 1, insertion.size(), file) != insertion.size() || std::fflush(file) != 0)
        error = errno ? errno : EIO;
    if (std::fclose(file) != 0 && !error)
        error = errno ? errno : EIO;
    if (error) {
        *why = path + ": " + std::strerror(error);
        std::remove(path.c_str());
        return false;
    }
    return true;
}

// Two readers of one identity's images can overlap: the card's window and
// cover, and its world, are read by different threads. Each holds the marker
// while it reads: the first writes it, a second reader of the same insertion
// only counts itself, and the last one done removes it, so one reader
// finishing first never leaves the other unguarded. A reader whose hold()
// is false holds nothing and must not release().
struct Holders {
    std::mutex lock;
    int count = 0;
    std::string insertion;
};

inline Holders &holders()
{
    static Holders shared;
    return shared;
}

inline bool hold(const std::string &path, const std::string &insertion, std::string *why)
{
    Holders &h = holders();
    std::lock_guard<std::mutex> guard(h.lock);
    if (h.count > 0 && h.insertion == insertion) {
        ++h.count;
        return true;
    }
    if (!write(path, insertion, why))
        return false;
    ++h.count;
    h.insertion = insertion;
    return true;
}

inline void release(const std::string &path)
{
    Holders &h = holders();
    std::lock_guard<std::mutex> guard(h.lock);
    if (h.count > 0 && --h.count == 0) {
        std::remove(path.c_str());
        h.insertion.clear();
    }
}

// The insertion an export's folder is of, from the folder's name: the card
// service names it <insertion>.<attempt>, the insertion sixteen lowercase
// hexadecimal digits, the attempt one to nine digits. Empty for any other
// name (the laboratory's scenes): nothing then names an insertion to guard.
inline std::string insertionOf(const std::string &exportName)
{
    constexpr std::size_t kInsertion = 16;
    if (exportName.size() < kInsertion + 2 || exportName.size() > kInsertion + 10 || exportName[kInsertion] != '.')
        return {};
    for (std::size_t i = 0; i < exportName.size(); ++i) {
        const char c = exportName[i];
        const bool digit = c >= '0' && c <= '9';
        if (i < kInsertion ? !(digit || (c >= 'a' && c <= 'f')) : i > kInsertion && !digit)
            return {};
    }
    return exportName.substr(0, kInsertion);
}

// What a start learns from a marker. `insertion`: the one to skip, empty
// for none; `found`: a marker named it at this start (the last start ended
// while it read that insertion's images), rather than an earlier one.
struct Skip {
    std::string insertion;
    bool found = false;
};

// The text of a marker: its first bytes (an insertion is sixteen), without
// the white space round them. False when `path` is not a file that can be
// read (nothing there, or a directory).
inline bool read(const std::string &path, std::string *text)
{
    text->clear();
    std::FILE *file = path.empty() ? nullptr : std::fopen(path.c_str(), "rb");
    if (!file)
        return false;
    char bytes[64];
    const std::size_t count = std::fread(bytes, 1, sizeof bytes, file);
    const bool failed = std::ferror(file) != 0;
    std::fclose(file);
    if (failed)
        return false;
    const char *const space = " \t\r\n";
    text->assign(bytes, count);
    text->erase(0, text->find_first_not_of(space));
    text->erase(text->find_last_not_of(space) + 1);
    return true;
}

// Called once, as the shell starts. A marker at `marker` is consumed, and
// the insertion it names kept at `kept`, replacing the one kept before:
// the shell's later starts, which find no marker, skip it too, for as long
// as the runtime directory lives (a card put in again is another
// insertion). Something at `marker` that is not a file is left as it is.
inline Skip resume(const std::string &marker, const std::string &kept)
{
    Skip skip;
    std::string named;
    if (read(marker, &named)) {
        std::remove(marker.c_str());
        if (!named.empty()) {
            std::string unkept;
            write(kept, named, &unkept);   // not kept: skipped at this start all the same
            skip.insertion = named;
            skip.found = true;
            return skip;
        }
    }
    read(kept, &skip.insertion);
    return skip;
}

} // namespace decodingmarker
