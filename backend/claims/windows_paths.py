"""Validate Windows source paths without accessing the Windows filesystem."""
from pathlib import PureWindowsPath
from rest_framework.exceptions import ValidationError

def validate_windows_path(value, kind):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({kind: "Windows folder path is required."})

    value = value.strip()

    if len(value) > 2000:
        raise ValidationError({kind: "Folder path is too long."})

    path = PureWindowsPath(value)

    if not path.drive or not path.root:
        raise ValidationError({
            kind: "Enter an absolute Windows folder path, such as D:\\Claims\\835."
        })

    if value.startswith("\\\\"):
        raise ValidationError({
            kind: "Network paths are not supported yet."
        })

    if ".." in path.parts:
        raise ValidationError({
            kind: "Parent directory traversal is not allowed."
        })

    return str(path)
