"""Conservative container identification for avoiding unnecessary remuxing."""

from pathlib import Path

from mushin.media.probe import MediaInfo


def matches_container(path: Path, info: MediaInfo, container: str) -> bool:
    """Require evidence beyond ffprobe's shared demuxer aliases.

    Unrecognized headers/brands take the normal remux path. Never use the
    logical filename as evidence. Read at most 4 KiB of the EBML header.
    """
    if container not in info.format.names:
        return False
    if container in ("matroska", "webm"):
        with path.open("rb") as stream:
            header = stream.read(4096)
        return _ebml_doctype(header) == container.encode("ascii")
    if container == "mp4":
        brand = info.format.tags.get("major_brand", "").strip()
        # QuickTime, 3GP and JPEG 2000 share the MOV/MP4 demuxer. Only
        # positively identified MP4 brands qualify for the no-op path.
        return brand in {"isom", "iso2", "iso3", "iso4", "iso5", "iso6", "iso7", "iso8", "iso9",
                         "mp41", "mp42", "avc1", "M4V", "M4A", "MSNV", "dash", "cmfc", "cmfs"}
    return False


def _vint(data: bytes, offset: int, *, identifier: bool = False) -> tuple[int, int]:
    if offset >= len(data) or data[offset] == 0:
        raise ValueError("Missing EBML variable integer")
    # Leading zero bits locate the marker bit, which encodes the byte width.
    width = 8 - data[offset].bit_length() + 1
    if width > (4 if identifier else 8) or offset + width > len(data):
        raise ValueError("Invalid EBML variable integer")
    value = int.from_bytes(data[offset:offset + width], "big")
    # IDs retain the marker bit; sizes remove it and reserve all ones for
    # unknown length, which cannot establish a bounded header for a no-op.
    if not identifier:
        value &= (1 << (7 * width)) - 1
        if value == (1 << (7 * width)) - 1:
            raise ValueError("Unknown EBML element size")
    return value, offset + width


def _ebml_doctype(header: bytes) -> bytes | None:
    # RFC 9559: Matroska has EBML Header ID 0x1A45DFA3 and DocType
    # element ID 0x4282. Parse elements rather than searching arbitrary bytes.
    if not header.startswith(b"\x1a\x45\xdf\xa3"):
        return None
    try:
        size, offset = _vint(header, 4)
        end = offset + size
        if end > len(header):
            return None
        document_type = None
        while offset < end:
            identifier, offset = _vint(header[:end], offset, identifier=True)
            size, offset = _vint(header[:end], offset)
            if offset + size > end:
                return None
            if identifier == 0x4282:
                if document_type is not None:
                    # Multiple declarations are ambiguous, so do not skip remuxing.
                    return None
                document_type = header[offset:offset + size]
            offset += size
        return document_type
    except ValueError:
        return None
