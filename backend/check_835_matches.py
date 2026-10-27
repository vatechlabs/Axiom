from collections import Counter
from claims.models import Client, Claim, EdiClaimIndex

client = Client.objects.get(code="WGS")

counts = Counter(
    value.strip().upper()
    for value in Claim.objects.filter(client=client).values_list("claim_number", flat=True)
    if value
)

indexes = EdiClaimIndex.objects.filter(
    edi_file__client=client,
    edi_file__kind="835",
    identifier_type="payer_control"
)

total = 0
unique = 0
ambiguous = 0
matched_claims = set()

for value in indexes.values_list("claim_identifier", flat=True).iterator(chunk_size=1000):
    if not value or len(value) != 15:
        continue

    claim_number = value[2:13].upper()

    if claim_number in counts:
        total += 1
        matched_claims.add(claim_number)

        if counts[claim_number] == 1:
            unique += 1
        else:
            ambiguous += 1

print("Candidate 835 occurrences:", total)
print("Unambiguous occurrences:", unique)
print("Ambiguous occurrences:", ambiguous)
print("Distinct matched WGS claims:", len(matched_claims))
