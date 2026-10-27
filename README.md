# LAG Claims Portal — React / Django / PostgreSQL

## Status
**Implementation starter, not a verified production release.** React application, client-scoped Django APIs,
Postgres storage, and automatic error/EDI folder indexing are included. Integration testing requires Docker,
real column mapping from decrypted LAG reports, and a sample 837. Do not use real PHI until security review.

## Input folders
```
client-data/XYZ/error_reports
client-data/XYZ/835
client-data/XYZ/837
client-data/PQR/error_reports
client-data/PQR/835
client-data/PQR/837
```
Mount the production directories at `/data/clients/CLIENT/{error_reports,835,837}` as READ ONLY.
Uploaded LAG XLSX is encrypted in transit using HTTPS in production; original binary is stored in PostgreSQL.
For encrypted Excel, enter the password in the upload screen; it is used in memory and is NOT stored.

## LAG import assumptions
Only sheets `LOCAL CLMS`, `LOCAL ADJ`, `ITS CLMS`, `ITS ADJ` are read. **Fund is column I.**
The uploaded real workbook is encrypted and its headers could not be verified in this environment.
If auto-discovery fails, configure `LAG_COLUMN_MAP_JSON` with verified Excel column letters, for example
`{"DCN":"B","SUBMITTER":"C","TPA_AGE":"Q","TOTAL_AGE":"R"}`.
**The letters in this example are placeholders and must NOT be used without checking the actual workbook.**
Missing claim identifiers or age headers cause import to fail, not silently invent values.

## Start locally
```
cp .env.example .env
# edit passwords/secret
# create admin/demo fixtures only in local environment
docker compose up --build -d
docker compose exec backend python manage.py makemigrations claims
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_demo
```
For local development, the backend generates and applies initial migrations automatically.
**Before deployment:** commit reviewed Django migrations rather than generating them during startup.
Open http://localhost:5173. `seed_demo` creates demo users and synthetic records: replace demo credentials before deployment.

## Indexing
`docker compose exec backend python manage.py index_client_folder --client XYZ`.
A dedicated `indexer` service also polls both clients. It hashes files and reindexes changed sources;
error observations are immutable per source. 835 parser reads `CLP01` (submitter) and `CLP07` (payer control).
837 parser is ready for `CLM01` but must be verified with sample files. Files themselves remain on disk.
API / error history / notes / Excel export are client-scoped.

## Key caution
EDI preview serves a CLAIM-SPECIFIC EXCERPT (not a standards-valid standalone 835 or 837 interchange).
Full production 837 extraction needs envelope rebuilding and explicit 837 format validation.
The file scanner currently only handles this supplied fixed-width error-report layout.
Add audit logs, stronger auth/session controls, dedicated reverse proxy, secure file access, backups,
retention policies, pagination and importer test coverage before live rollout.
