"""Windows folder configuration for the local Axiom agent."""
import json
import os
from pathlib import Path

from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ClientFolderConfig, Membership
from .permissions import allowed_client
from .windows_paths import validate_windows_path

CONFIG_FILE = Path("/agent-config/folders.json")

FIELDS = {
    "error_reports": "windows_error_reports_path",
    "835": "windows_835_path",
    "837": "windows_837_path",
    "recon": "windows_recon_path",
    "recon_musgrove": "windows_recon_musgrove_path",
    "recon_abc": "windows_recon_abc_path",
}


def publish_agent_config():
    """Write all configured clients to the local agent configuration."""
    clients = {}

    for cfg in ClientFolderConfig.objects.select_related("client"):
        paths = {
            kind: getattr(cfg, field)
            for kind, field in FIELDS.items()
        }

        # Preserve the original EDI sources and sync only the RECON folders
        # applicable to this client; never mix WGS sub-client folders.
        if cfg.client.code == 'WGS':
            paths.pop('recon', None)
        else:
            paths.pop('recon_musgrove', None)
            paths.pop('recon_abc', None)
        if any(paths.values()):
            clients[cfg.client.code] = paths

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    temp = CONFIG_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"clients": clients}, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, CONFIG_FILE)


@api_view(["GET", "PUT"])
def windows_folder_settings(request):
    client = allowed_client(request)

    editable = (
        request.user.is_superuser
        or Membership.objects.filter(
            user=request.user,
            client=client,
            role="admin",
        ).exists()
    )

    cfg, _ = ClientFolderConfig.objects.get_or_create(client=client)

    if request.method == "PUT":
        if not editable:
            return Response(
                {"detail": "Only client administrators can edit folders."},
                status=403,
            )

        paths = request.data.get("paths")

        if not isinstance(paths, dict) or not {"error_reports", "835", "837"}.issubset(paths) or not set(paths).issubset(FIELDS):
            return Response(
                {"detail": "Provide error_reports, 835 and 837 paths."},
                status=400,
            )

        checked = {}
        for kind in FIELDS:
            value = paths.get(kind, '')
            if kind.startswith('recon'):
                checked[kind] = validate_windows_path(value, kind) if value else ''
            else:
                checked[kind] = validate_windows_path(value, kind)

        with transaction.atomic():
            for kind, field in FIELDS.items():
                setattr(cfg, field, checked[kind])
            if client.code == 'WGS':
                # The legacy recon field is not a WGS folder.
                cfg.windows_recon_path = ''
            elif checked['recon'] and not cfg.recon_path:
                cfg.recon_path = f"/data/clients/{client.code}/recon"
            cfg.save()
            transaction.on_commit(publish_agent_config)

    return Response({
        "client": client.code,
        "paths": {
            kind: getattr(cfg, field)
            for kind, field in FIELDS.items()
        },
        "editable": editable,
    })
