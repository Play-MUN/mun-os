// Host-side checks of the scan the shell makes of a PNG's chunks before it
// opens it (src/pngchunks.h): an image as common tools write it is within
// the bounds; one whose header carries hundreds of compressed text chunks
// is refused from its chunk headers alone, without reading a chunk's body;
// each bound holds at its limit and not past it; the walk stops where a
// reader would. The device is a stand-in with QIODevice's size(), seek()
// and read(), which also counts what was read. Any C++20 compiler; no Qt.
// Run by `make check` (tests/Makefile here).
#include "pngchunks.h"

#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace {

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

using Bytes = std::vector<unsigned char>;

// What the scan is given: the three members of QIODevice it uses, over
// bytes in memory. A read past the end is short, as a file's.
struct Device {
    explicit Device(Bytes bytes) : data(std::move(bytes)) {}
    Bytes data;
    long long position = 0;
    long long reads = 0, bytesRead = 0, largestRead = 0;
    bool failSeek = false, failRead = false;

    long long size() const { return static_cast<long long>(data.size()); }
    bool seek(long long to)
    {
        if (failSeek || to < 0 || to > size())
            return false;
        position = to;
        return true;
    }
    long long read(char *out, long long most)
    {
        if (failRead)
            return -1;
        const long long n = std::min(most, size() - position);
        std::memcpy(out, data.data() + position, static_cast<std::size_t>(n));
        position += n;
        ++reads;
        bytesRead += n;
        largestRead = std::max(largestRead, most);
        return n;
    }
};

void put32(Bytes &out, std::uint32_t value)
{
    for (int shift = 24; shift >= 0; shift -= 8)
        out.push_back(static_cast<unsigned char>(value >> shift));
}

// A chunk: its length, type, a body of `length` bytes and a CRC's place
// (the scan reads neither the body nor the CRC).
Bytes chunk(const char *type, std::uint32_t length)
{
    Bytes out;
    put32(out, length);
    out.insert(out.end(), type, type + 4);
    out.insert(out.end(), length, static_cast<unsigned char>(0xAB));
    put32(out, 0);
    return out;
}

Bytes operator+(Bytes a, const Bytes &b)
{
    a.insert(a.end(), b.begin(), b.end());
    return a;
}

Bytes times(const Bytes &one, int count)
{
    Bytes out;
    for (int i = 0; i < count; ++i)
        out.insert(out.end(), one.begin(), one.end());
    return out;
}

const Bytes kSignature = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};

// signature, IHDR, `before`, the image data, `after`, IEND
Bytes png(const Bytes &before = {}, const Bytes &after = {})
{
    return kSignature + chunk("IHDR", 13) + before + chunk("IDAT", 4096) + chunk("IDAT", 100) + after
           + chunk("IEND", 0);
}

bool within(const Bytes &bytes)
{
    Device device(bytes);
    return pngchunks::withinBounds(device);
}

void ordinaryImages()
{
    check("an image with no ancillary chunk is within the bounds", within(png()));
    // As common tools write it: resolution, gamma, chromaticities, an sRGB
    // profile, a background, a time, EXIF, the program's name, a comment,
    // an XMP packet and a compressed text; text after the image data too.
    const Bytes usual = chunk("pHYs", 9) + chunk("gAMA", 4) + chunk("cHRM", 32) + chunk("iCCP", 2600) + chunk("bKGD", 6)
                        + chunk("tIME", 7) + chunk("eXIf", 2052) + chunk("tEXt", 38) + chunk("tEXt", 23)
                        + chunk("iTXt", 7400) + chunk("zTXt", 40);
    check("an image with ordinary metadata is within the bounds", within(png(usual, chunk("tEXt", 30))));
    Device device(png(usual));
    device.position = 5;
    check("the device is left at its start for the reader that follows",
          pngchunks::withinBounds(device) && device.position == 0);
}

void theHostileImage()
{
    // The image that makes its reader hold gigabytes: 536 compressed text
    // chunks of 7,784 bytes (each inflates to 8 MB), four MiB in all, as the
    // card service's checker and the shell once accepted it. And its control,
    // the same image without them.
    const Bytes hostile = png(times(chunk("zTXt", 7784), 536));
    check("the image is within a Shape image's size", hostile.size() < 4 * 1024 * 1024 && hostile.size() > 4000000,
          std::to_string(hostile.size()));
    Device device(hostile);
    check("hundreds of compressed text chunks are refused", !pngchunks::withinBounds(device));
    check("from chunk headers alone: eight bytes a read, no body",
          device.largestRead == 8 && device.bytesRead == 8 * device.reads, std::to_string(device.bytesRead));
    check("and no further than the first bound exceeded", device.reads <= 8, std::to_string(device.reads));
    check("the device is left at its start all the same", device.position == 0);
    check("its control is within the bounds", within(png()));
    // The same as a cover (1 MiB): 134 chunks.
    check("a cover's worth of them is refused too", !within(png(times(chunk("zTXt", 7784), 134))));
    // After the image data they are read as well (when the image is decoded).
    check("text chunks after the image data count", !within(png({}, times(chunk("zTXt", 7784), 100))));
}

void eachBound()
{
    using namespace pngchunks;
    const int texts = static_cast<int>(kTextMaxChunks), all = static_cast<int>(kAncillaryMaxChunks);
    const std::uint32_t textBytes = static_cast<std::uint32_t>(kTextMaxBytes);
    const std::uint32_t allBytes = static_cast<std::uint32_t>(kAncillaryMaxBytes);
    for (const char *type : {"tEXt", "zTXt", "iTXt"}) {
        check(std::string(type) + ": as many as the bound", within(png(times(chunk(type, 10), texts))));
        check(std::string(type) + ": one more is refused", !within(png(times(chunk(type, 10), texts + 1))));
        check(std::string(type) + ": as many bytes as the bound", within(png(chunk(type, textBytes))));
        check(std::string(type) + ": one byte more is refused", !within(png(chunk(type, textBytes + 1))));
    }
    check("the kinds of text count together, before and after the image data",
          within(png(times(chunk("tEXt", 10), 6) + times(chunk("zTXt", 10), 5), times(chunk("iTXt", 10), 5)))
              && !within(png(times(chunk("tEXt", 10), 6) + times(chunk("zTXt", 10), 5), times(chunk("iTXt", 10), 6))));
    check("their bytes too", within(png(chunk("tEXt", textBytes / 2), chunk("zTXt", textBytes / 2)))
                                 && !within(png(chunk("tEXt", textBytes / 2), chunk("zTXt", textBytes / 2 + 1))));
    check("one colour profile", within(png(chunk("iCCP", 60000))));
    check("a second profile is refused", !within(png(chunk("iCCP", 600) + chunk("iCCP", 600))));
    // Chunks no reader knows count as any ancillary chunk.
    check("ancillary chunks of any kind: as many as the bound", within(png(times(chunk("prVt", 1), all))));
    check("one more is refused", !within(png(times(chunk("prVt", 1), all + 1))));
    check("as many bytes as the bound", within(png(chunk("prVt", allBytes))));
    check("one byte more is refused", !within(png(chunk("prVt", allBytes + 1))));
    check("every kind adds to the bytes", !within(png(chunk("eXIf", allBytes) + chunk("pHYs", 9))));
    check("text within its own bound counts towards all",
          !within(png(chunk("tEXt", textBytes) + chunk("prVt", allBytes - textBytes + 1))));
    // The image itself is not counted, however many chunks it is in.
    check("critical chunks are not counted", within(kSignature + chunk("IHDR", 13) + chunk("PLTE", 768)
                                                    + times(chunk("IDAT", 16), 5000) + chunk("IEND", 0)));
}

void whereTheWalkStops()
{
    const Bytes note = chunk("tEXt", 10);
    check("nothing after IEND is read", within(png() + times(note, 40)));
    // Cut short: judged on the chunks it holds, the last one by its header.
    Bytes cut = kSignature + chunk("IHDR", 13) + times(note, 40);
    cut.resize(8 + 25 + 30 * note.size() + 5);
    check("a file cut short is judged on what it holds", !within(cut));
    cut.resize(8 + 25 + 10 * note.size() + 5);
    check("and is within the bounds when that is", within(cut));
    Bytes declared = kSignature + chunk("IHDR", 13);
    put32(declared, static_cast<std::uint32_t>(pngchunks::kTextMaxBytes) + 1);
    declared.insert(declared.end(), {'z', 'T', 'X', 't', 1, 2, 3});
    check("a chunk is counted with the length it declares, whatever is there", !within(declared));
    Bytes endless = kSignature + chunk("IHDR", 13);
    put32(endless, 0x80000000u);
    endless.insert(endless.end(), {'t', 'E', 'X', 't'});
    check("a length no chunk may have ends the walk", within(endless + times(note, 40)));
    // Not a PNG, or nothing at all: not this scan's to say; its reader fails.
    check("an empty file is left to its reader", within({}));
    check("a signature alone is left to its reader", within(kSignature));
    check("bytes that are not chunks are left to their reader", within(Bytes(5000, 0x00)));
    Device unseekable(png(times(note, 40)));
    unseekable.failSeek = true;
    check("a device that cannot be read ends the walk", pngchunks::withinBounds(unseekable));
    Device unreadable(png(times(note, 40)));
    unreadable.failRead = true;
    check("as does one whose read fails", pngchunks::withinBounds(unreadable));
}

} // namespace

int main()
{
    ordinaryImages();
    theHostileImage();
    eachBound();
    whereTheWalkStops();
    std::printf("PNG chunk scan: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed == 0 ? 0 : 1;
}
