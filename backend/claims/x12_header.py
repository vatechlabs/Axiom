"""Read an X12 ISA header with an optional UTF-8 BOM."""
from codecs import BOM_UTF8


def read_isa(stream):
    start = len(BOM_UTF8) if stream.read(3) == BOM_UTF8 else 0
    stream.seek(start)
    header = stream.read(106)
    if len(header) != 106 or not header.startswith(b'ISA'):
        raise ValueError('Invalid ISA envelope')
    return header, start
