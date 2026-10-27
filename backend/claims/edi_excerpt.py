"""Seek-based, bounded claim-specific EDI excerpt from local/UNC paths."""

def extract_claim_excerpt(path, index, kind, max_bytes=1024*1024):
    if index.start_byte is None:
        raise ValueError('Reindex legacy EDI source to obtain claim offset')
    marker=b'CLP' if kind=='835' else b'CLM'
    with open(path,'rb') as stream:
        header=stream.read(106)
        if len(header)<106 or not header.startswith(b'ISA'):
            raise ValueError('Invalid ISA envelope')
        sep=bytes([header[105]])
        elem=bytes([header[3]])
        stream.seek(index.start_byte)
        pending=b''
        pieces=[]
        total=0
        first=True
        while total<max_bytes:
            chunk=stream.read(min(65536,max_bytes-total+65536))
            if not chunk:break
            pending+=chunk
            while sep in pending:
                raw,pending=pending.split(sep,1)
                tag=raw.strip().split(elem,1)[0]
                if first:
                    if tag!=marker:raise ValueError('Indexed claim offset is stale')
                    first=False
                elif tag in (marker,b'SE',b'GE',b'IEA',b'HL'):
                    return b'NOTICE: Claim-specific excerpt, not a complete interchange.\n'+b''.join(pieces)
                if total+len(raw)+2>max_bytes:
                    return b'NOTICE: Claim-specific excerpt, not a complete interchange.\n'+b''.join(pieces)+b'[EXCERPT TRUNCATED]'
                pieces.append(raw.strip()+sep+b'\n')
                total+=len(raw)+2
            if len(pending)>max_bytes:
                break
        if first:raise ValueError('Indexed claim occurrence not found')
    return b'NOTICE: Claim-specific excerpt, not a complete interchange.\n'+b''.join(pieces)+b'[EXCERPT TRUNCATED OR SOURCE ENDED]'


# A complete ST..SE is distinct from a CLP/CLM-specific excerpt.
# Iterate from disk in bounded chunks rather than reading the full EDI file.
def extract_transaction(path, index, kind, max_bytes=2*1024*1024):
    if index.start_byte is None:
        raise ValueError('Reindex source to obtain claim offset')
    marker = b'CLP' if kind == '835' else b'CLM'
    with open(path, 'rb') as stream:
        header = stream.read(106)
        if len(header) < 106 or not header.startswith(b'ISA'):
            raise ValueError('Invalid ISA envelope')
        sep, elem = bytes([header[105]]), bytes([header[3]])
        stream.seek(0)
        pending = b''
        absolute = 0
        transaction = None
        contains_claim = False
        too_large = False
        # Reads whole file incrementally, keeping only the active transaction.
        while True:
            chunk = stream.read(65536)
            if not chunk:
                if transaction is not None and contains_claim:
                    raise ValueError('ST transaction has no closing SE segment')
                raise ValueError('Enclosing ST-SE transaction not found for indexed claim')
            pending += chunk
            while True:
                split = pending.find(sep)
                if split < 0:
                    if len(pending) > max_bytes:
                        raise ValueError('EDI segment exceeds preview limit')
                    break
                segment = pending[:split]
                start = absolute
                absolute += split + 1
                pending = pending[split+1:]
                stripped = segment.strip()
                tag = stripped.split(elem,1)[0]
                line = stripped + sep + b'\n'
                if tag == b'ST':
                    transaction = bytearray()
                    contains_claim = False
                    too_large = False
                if transaction is not None:
                    if start == index.start_byte:
                        if tag != marker:
                            raise ValueError('Indexed claim offset is stale')
                        contains_claim = True
                    if not too_large:
                        if len(transaction) + len(line) > max_bytes:
                            too_large = True
                            transaction.clear()
                        else:
                            transaction.extend(line)
                    if tag == b'SE':
                        if contains_claim:
                            if too_large:
                                raise ValueError('Transaction exceeds 2 MB preview limit; download original file')
                            return bytes(transaction)
                        transaction = None
                        contains_claim = False
                elif start == index.start_byte:
                    raise ValueError('Indexed claim is not inside ST-SE transaction')
