from collections import defaultdict
from claims.models import Client, Claim, EdiClaimIndex

client = Client.objects.get(code="WGS")

by_id = defaultdict(list)

for claim in Claim.objects.filter(client=client).only("id", "claim_number"):
    if claim.claim_number:
        by_id[claim.claim_number.strip().upper()].append(claim.id)

matched = 0
skipped = 0
examples = []

indexes = EdiClaimIndex.objects.filter(
    edi_file__client=client,
    edi_file__kind="835",
    identifier_type="payer_control"
)

for index in indexes.values_list(
    "claim_identifier", flat=True
).iterator(chunk_size=1000):

    identifier = (index or "").strip().upper()

    if len(identifier) != 15 or not identifier.startswith("20"):
        skipped += 1
        continue

    matches = by_id.get(identifier[2:13], [])

    if len(matches) == 1:
        matched += 1
        if len(examples) < 3:
            examples.append((identifier, matches[0]))

print("835 records eligible for linking:", matched)
print("Records skipped by format:", skipped)
print("Example matches:", examples)
