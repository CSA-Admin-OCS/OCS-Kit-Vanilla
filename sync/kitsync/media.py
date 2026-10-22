"""Strip text metadata from PNG and JPEG files (stdlib only).

Design tools (Canva, Photoshop) write the author's account/brand into image metadata, e.g.
``brand=Del Norte High``. Pixels are untouched.
"""

import struct

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def strip_png(data):
    if not data.startswith(PNG_SIG):
        return data
    out = [PNG_SIG]
    i = len(PNG_SIG)
    while i + 8 <= len(data):
        (length,) = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            return data  # truncated / unusual: leave alone
        if ctype not in PNG_DROP:
            out.append(data[i:end])
        i = end
        if ctype == b"IEND":
            break
    return b"".join(out)


def strip_jpeg(data):
    if not data.startswith(b"\xff\xd8"):
        return data
    out = [b"\xff\xd8"]
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return data
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan: copy the rest verbatim
            out.append(data[i:])
            return b"".join(out)
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        seg = data[i:i + 2 + length]
        # APP1 (EXIF/XMP), APP13 (IPTC/Photoshop), COM
        if marker not in (0xE1, 0xED, 0xFE):
            out.append(seg)
        i += 2 + length
    return data


def strip_metadata(path, data):
    p = path.lower()
    if p.endswith(".png"):
        return strip_png(data)
    if p.endswith((".jpg", ".jpeg")):
        return strip_jpeg(data)
    return data
