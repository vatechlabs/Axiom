"""Highmark MIR placeholder regression tests (AXIOM_MIR_NA_PLACEHOLDER_V1)."""
from django.test import SimpleTestCase
from .mir_processing import parse_mir


def row(identifier, prefix=b'MO', length=637):
    data = bytearray(b' ' * length)
    data[0:2] = prefix
    encoded = identifier.encode('ascii')
    if len(encoded) > 17:
        raise ValueError('identifier too long for test')
    data[2:2 + len(encoded)] = encoded
    return bytes(data)


class MirPlaceholderTests(SimpleTestCase):
    def test_na_preserved_and_unlinked(self):
        original = row('N/A')
        rows, controls = parse_mir(row('CLAIM123') + b'\r\n' + original + b'\r\n', received=False)
        self.assertEqual(controls, 0)
        self.assertEqual([(x[0], x[1]) for x in rows], [(1, 'CLAIM123'), (2, '')])
        self.assertEqual(rows[1][3].encode('cp1252'), original)

    def test_case_insensitive_na(self):
        rows, _ = parse_mir(row('n/a'), received=False)
        self.assertEqual(rows[0][1], '')

    def test_received_na_preserves_error_field(self):
        data = bytearray(row('N/A', prefix=b'MI'))
        data[207:217] = b'ERR12     '
        rows, _ = parse_mir(bytes(data), received=True)
        self.assertEqual(rows[0][1], '')
        self.assertEqual(rows[0][2], 'ERR12')

    def test_blank_identifier_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'invalid claim identifier'):
            parse_mir(row(''), received=False)

    def test_other_invalid_identifier_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'invalid claim identifier'):
            parse_mir(row('A/B'), received=False)
