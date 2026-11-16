// pngchunks says, from a PNG's chunk headers alone, whether the file carries
// more ancillary data than the console accepts: the bounds the checker holds
// every image to (tools/mun-card, mun_card/validate.py, PNG_*), held here
// again so that the shell does not lean on the card service alone.
//
// It is asked before any image reader opens a file that came from a card. A
// PNG reader keeps every text chunk and the colour profile it meets while it
// reads the header (for a size, a format or the pixels) and inflates the
// compressed ones, outside Qt's allocation limit, which covers pixels only:
// a file of a few MiB with hundreds of compressed text chunks makes its
// reader hold gigabytes before a pixel is decoded.
//
// Only chunk headers are read, eight bytes each, at the offsets the lengths
// give, and never a chunk's body: the work is bounded by the file's size and
// stops at the first bound exceeded. What is counted is what is stored. What
// the compressed chunks inflate to is the checker's to bound (it inflates
// them, up to its bound); here it follows from the stored one: deflate
// expands at most 1032 times, so kTextMaxBytes of text is 33 MiB at the very
// most, and a reader inflates one profile only.
//
// The walk is a reader's: by the lengths, from the signature to IEND or to
// where the file ends or stops making sense, a chunk counted with the length
// it declares. Whether the file is a PNG at all is not judged: one that is
// not fails in its reader, as before.
//
// No Qt: checked on the host (tests/pngchunks_test.cpp).
#pragma once

#include <cstdint>

namespace pngchunks {

// Every ancillary chunk, of any kind (a lowercase first letter).
constexpr std::uint64_t kAncillaryMaxChunks = 64;
constexpr std::uint64_t kAncillaryMaxBytes = 256 * 1024;
// Of them, the text chunks: tEXt, zTXt, iTXt.
constexpr std::uint64_t kTextMaxChunks = 16;
constexpr std::uint64_t kTextMaxBytes = 32 * 1024;
// And the colour profile: iCCP.
constexpr std::uint64_t kProfileMaxChunks = 1;

struct Counts {
    std::uint64_t ancillary = 0, ancillaryBytes = 0;
    std::uint64_t text = 0, textBytes = 0;
    std::uint64_t profiles = 0;

    bool within() const
    {
        return ancillary <= kAncillaryMaxChunks && ancillaryBytes <= kAncillaryMaxBytes && text <= kTextMaxChunks
               && textBytes <= kTextMaxBytes && profiles <= kProfileMaxChunks;
    }

    // One chunk's header: its type (four bytes) and the length it declares.
    void add(const unsigned char *type, std::uint32_t length)
    {
        if (!(type[0] & 0x20))
            return;   // critical: the image itself
        ++ancillary;
        ancillaryBytes += length;
        if (is(type, "tEXt") || is(type, "zTXt") || is(type, "iTXt")) {
            ++text;
            textBytes += length;
        } else if (is(type, "iCCP")) {
            ++profiles;
        }
    }

    static bool is(const unsigned char *type, const char *name)
    {
        return type[0] == static_cast<unsigned char>(name[0]) && type[1] == static_cast<unsigned char>(name[1])
               && type[2] == static_cast<unsigned char>(name[2]) && type[3] == static_cast<unsigned char>(name[3]);
    }
};

// True when the PNG in `device` is within every bound. `device` is anything
// with QIODevice's size(), seek() and read() that can be read at any
// position (a QFile, a QBuffer), open for reading; it is left at its start,
// for the reader that follows.
template <typename Device>
bool withinBounds(Device &device)
{
    const long long size = device.size();
    Counts counts;
    long long offset = 8;   // past the signature
    unsigned char head[8];
    while (size - offset >= 8 && counts.within()) {
        if (!device.seek(offset) || device.read(reinterpret_cast<char *>(head), 8) != 8)
            break;
        const std::uint32_t length = (std::uint32_t(head[0]) << 24) | (std::uint32_t(head[1]) << 16)
                                     | (std::uint32_t(head[2]) << 8) | std::uint32_t(head[3]);
        if (length > 0x7FFFFFFFu || Counts::is(head + 4, "IEND"))
            break;
        counts.add(head + 4, length);
        // header, body and CRC; a body that runs past the end ends the walk
        const long long next = 12 + static_cast<long long>(length);
        if (next > size - offset)
            break;
        offset += next;
    }
    device.seek(0);
    return counts.within();
}

} // namespace pngchunks
