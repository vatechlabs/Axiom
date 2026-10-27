from django.db import transaction
from claims.models import Client, Claim, EdiClaimIndex, EdiDocument

client = Client.objects.get(code="WGS")

index = EdiClaimIndex.objects.filter(
    edi_file__client=client,
    edi_file__kind="835",
    identifier_type="payer_control",
    claim_identifier="2026017BE577480"
).select_related("edi_file").first()

if index is None:
    raise RuntimeError("Test EDI index not found")

claim_number = index.claim_identifier.strip().upper()[2:13]
claim = Claim.objects.get(client=client, claim_number__iexact=claim_number)

print("Matching claim ID:", claim.id)

with transaction.atomic():
    document = EdiDocument.objects.create(
        claim=claim,
        edi_file=index.edi_file,
        edi_index=index,
        kind=index.edi_file.kind,
        original_name=index.edi_file.filename
    )

    print("Temporary EDI document ID:", document.id)
    print("Temporary link exists:", EdiDocument.objects.filter(pk=document.id).exists())

    transaction.set_rollback(True)

print("Link rolled back; no permanent record created.")
