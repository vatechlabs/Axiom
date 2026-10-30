"""Highmark-only 837 identifiers; no Django dependency.

Only claim-level REF*F8 / REF*D9 / REF*9C values are indexed.
All identifiers retain their parent's CLM position for claim excerpt extraction.
"""
from pathlib import Path
import re

HIGHMARK_CODES = frozenset(('HIGHMARK', 'HMRK', 'HM'))
HIGHMARK_REF_TYPES = {'F8': 'highmark_ref_f8', 'D9': 'highmark_ref_d9', '9C': 'highmark_ref_9c'}

def highmark_extensionless_837(path):
    """Recognize observed IPA files; do not accept arbitrary extensionless files."""
    p = Path(path)
    return not p.suffix and bool(re.fullmatch(r'IPA\d{7}[IP]', p.name, flags=re.I))

def highmark_837_refs(path, segment_iterator):
    """Yield (identifier, type, CLM segment number, CLM byte offset)."""
    with open(path, 'rb') as stream:
        isa = stream.read(106)
    if len(isa) != 106 or not isa.startswith(b'ISA'):
        raise ValueError('Not an X12 interchange')
    element = chr(isa[3])
    position = None
    claim_level = False
    seen = set()
    for offset, number, segment in segment_iterator(path):
        fields = segment.decode('latin-1').strip().split(element)
        tag = fields[0]
        if tag == 'CLM':
            position = (number, offset)
            claim_level = True
            seen = set()
            if len(fields) > 1 and fields[1].strip():
                yield fields[1].strip(), 'submitter', number, offset
        elif tag in ('ISA', 'GS', 'ST', 'SE', 'GE', 'IEA', 'HL', 'LX'):
            if tag != 'LX':
                position = None
            claim_level = False
        elif tag == 'REF' and claim_level and position is not None and len(fields) > 2:
            typ = HIGHMARK_REF_TYPES.get(fields[1].strip().upper())
            identifier = fields[2].strip()
            key = (typ, identifier.upper())
            if typ and identifier and len(identifier) <= 80 and key not in seen:
                seen.add(key)
                yield identifier, typ, position[0], position[1]
