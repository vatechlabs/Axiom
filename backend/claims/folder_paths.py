from pathlib import Path
from django.conf import settings
from rest_framework.exceptions import ValidationError

FIELDS = {'error_reports': 'error_reports_path', '835': 'edi_835_path', '837': 'edi_837_path'}


def allowed_roots():
    return [Path(root).resolve() for root in settings.EDI_ALLOWED_ROOTS]


def validate_path(value, kind):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({kind: 'A folder path is required.'})
    if len(value) > 2000:
        raise ValidationError({kind: 'Folder path is too long.'})
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValidationError({kind: 'Enter an absolute path on the backend server.'})
    path = path.resolve()
    if not any(path.is_relative_to(root) for root in allowed_roots()):
        raise ValidationError({kind: 'Outside approved data roots. Ask the server administrator to configure EDI_ALLOWED_ROOTS.'})
    if not path.is_dir():
        raise ValidationError({kind: 'Folder does not exist or is not accessible on the backend server.'})
    return str(path)


def client_paths(client):
    cfg = getattr(client, 'folder_config', None)
    root = Path(settings.EDI_ROOT).resolve() / client.code
    return {kind: str(Path(getattr(cfg, attr)).resolve()) if cfg and getattr(cfg, attr) else str((root / kind).resolve()) for kind, attr in FIELDS.items()}
