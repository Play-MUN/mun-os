// decodingmarker is the file side of the shell's guard against an image
// whose decoding takes the shell down. Before an insertion's images are
// read, a marker naming the insertion is written in the runtime directory
// (which outlives the shell's restarts and not a reboot), and removed once
// they are read. A start that finds a marker knows which insertion it was
// reading when it ended, and skips it (Shape): the same image is not read
// again at every restart. A clean stop removes the markers itself
// (ExecStopPost in deploy/mun-shell.service).
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

} // namespace decodingmarker
