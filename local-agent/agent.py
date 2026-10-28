"""Axiom Windows local folder synchronization agent."""
import argparse
import time
import json
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "local-agent" / "runtime" / "folders.json"
DESTINATION = BASE_DIR / "client-data"

FOLDER_TYPES = {
    "error_reports": {".txt"},
    "835": {".835", ".edi", ".txt"},
    "837": {".837", ".edi", ".txt"},
    "recon": {".txt", ".csv", ".xlsx", ".xls", ".dat", ".recon", ".pa7"},
    "recon_musgrove": {".txt", ".csv", ".xlsx", ".xls", ".dat", ".recon"},
    "recon_abc": {".txt", ".csv", ".xlsx", ".xls", ".dat", ".recon"},
}


def sync_folder(client, kind, source_path):
    source = Path(source_path).expanduser().resolve(strict=True)

    if not source.is_dir():
        raise ValueError(f"Invalid folder: {source}")

    destination = (DESTINATION / client / kind).resolve()

    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError(
            "Source and destination folders must be separate and must not contain each other."
        )

    destination.mkdir(parents=True, exist_ok=True)

    copied = 0

    for file in source.rglob("*"):
        if file.is_symlink() or not file.is_file():
            continue

        if file.suffix.lower() not in FOLDER_TYPES[kind]:
            continue

        relative = file.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)

        stat = file.stat()

        if target.exists():
            old = target.stat()
            if old.st_size == stat.st_size and old.st_mtime_ns == stat.st_mtime_ns:
                continue

        temp = target.with_name(target.name + ".part")
        try:
            shutil.copy2(file, temp)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

        copied += 1

    return copied


def run():
    if not CONFIG_PATH.is_file():
        print("Agent configuration not found. Waiting for setup.")
        return

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))

    for client, paths in config.get("clients", {}).items():
        if (not isinstance(client, str) or
                not client.strip() or
                client in (".", "..") or
                "/" in client or "\\" in client or
                Path(client).is_absolute()):
            print("Skipping invalid client directory")
            continue

        allowed = (('error_reports','835','837','recon_musgrove','recon_abc')
                   if client == 'WGS' else ('error_reports','835','837','recon'))
        for kind in allowed:
            source = paths.get(kind)
            if not source:
                continue

            try:
                copied = sync_folder(client, kind, source)
                print(f"{client} / {kind}: {copied} files copied")
            except Exception as exc:
                print(f"{client} / {kind}: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    if args.watch:
        print("Axiom Windows Folder Agent started.", flush=True)
        try:
            while True:
                try:
                    run()
                except Exception as exc:
                    print(f"Agent error: {exc}", flush=True)
                time.sleep(max(10, args.interval))
        except KeyboardInterrupt:
            print("Axiom Agent stopped.", flush=True)
    else:
        run()
