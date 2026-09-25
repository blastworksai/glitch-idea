#!/usr/bin/env python3
"""Materialize the personal skill; never put durable idea data in its install tree."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


PRODUCT = "glitch-idea-personal"
MANIFEST = ".glitch-idea-install.json"


class InstallError(Exception):
    pass


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"Expected an object in {path}")
    return value


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def within(path, root):
    return path == root or root in path.parents


def tree_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise InstallError(f"Symlinks are not accepted in a skill tree: {path}")
        if path.is_file():
            result[relative.as_posix()] = path.read_bytes()
    return result


def effective_config(source, destination, explicit_store, recognized):
    installed_config = destination / "config.json"
    source_config = source / "config.json"
    if recognized:
        if not installed_config.is_file():
            raise InstallError(f"Recognized installation is missing {installed_config}; restore its saved config before upgrading so the durable store is not changed")
        config_path = installed_config
    else:
        config_path = source_config if source_config.exists() else source.parent / "config.json"
    config = read_json(config_path) if config_path.exists() else {}
    if recognized and (not isinstance(config.get("store_path"), str) or not config["store_path"].strip()):
        raise InstallError(f"Recognized installation has no valid store_path in {config_path}; restore its saved config before upgrading")
    store_value = explicit_store or config.get("store_path", str(source.parent / "ideas"))
    if not isinstance(store_value, str) or not store_value.strip():
        raise InstallError("store_path must be a nonempty path")
    store = Path(store_value).expanduser()
    if not store.is_absolute():
        store = (Path.cwd() if explicit_store else config_path.parent) / store
    store = store.resolve()
    if within(store, destination) or within(destination, store) or within(store, source):
        raise InstallError("The durable store must be outside the source and installed skill trees, and cannot contain the installation")
    argv = config.get("plan_validator_argv")
    if argv is not None and (not isinstance(argv, list) or not argv or
                             any(not isinstance(v, str) or not v.strip() for v in argv)):
        raise InstallError("plan_validator_argv must be null or a nonempty list of strings")
    timeout = config.get("validator_timeout_seconds", 30)
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise InstallError("validator_timeout_seconds must be an integer from 1 to 120")
    return {"store_path": str(store), "plan_validator_argv": argv,
            "validator_timeout_seconds": timeout}


def install(source, skills_root, store=None, dry_run=False):
    if source.is_symlink():
        raise InstallError("Source skill must be a materialized directory")
    source = source.expanduser().resolve()
    skills_root = skills_root.expanduser().resolve()
    destination = skills_root / "glitch-idea"
    if within(destination, source) or within(source, destination):
        raise InstallError("Source and installation must be separate trees")
    if destination.is_symlink():
        raise InstallError(f"Refusing a symlink destination: {destination}")
    if not (source / "SKILL.md").is_file() or not (source / "scripts" / "idea.py").is_file():
        raise InstallError("Source must contain SKILL.md and scripts/idea.py")
    recognized = destination.exists()
    if recognized:
        marker = destination / MANIFEST
        if not marker.is_file():
            raise InstallError(f"Unrelated skill already occupies {destination}; nothing changed")
        manifest = read_json(marker)
        if manifest.get("product") != PRODUCT or manifest.get("format_version") != 1:
            raise InstallError(f"Unrecognized installation at {destination}; nothing changed")
    config = effective_config(source, destination, store, recognized)
    payload = tree_files(source)
    payload.pop(MANIFEST, None)
    payload["config.json"] = json_bytes(config)
    current = tree_files(destination) if recognized else {}
    current.pop(MANIFEST, None)
    action = "unchanged" if recognized and current == payload else "upgraded" if recognized else "installed"
    result = {"ok": True, "action": action, "destination": str(destination),
              "store_path": config["store_path"], "dry_run": dry_run}
    if dry_run or action == "unchanged":
        return result
    skills_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".glitch-idea-stage-", dir=skills_root))
    backup = None
    try:
        hashes = {}
        for relative, content in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            hashes[relative] = hashlib.sha256(content).hexdigest()
        marker = {"product": PRODUCT, "format_version": 1,
                  "installed_at": datetime.now(timezone.utc).isoformat(),
                  "source": str(source), "files": hashes}
        with (stage / MANIFEST).open("wb") as stream:
            stream.write(json_bytes(marker))
            stream.flush()
            os.fsync(stream.fileno())
        if recognized:
            backup_home = skills_root / ".glitch-idea-backups"
            if backup_home.is_symlink():
                raise InstallError(f"Refusing a symlink backup directory: {backup_home}")
            backup_home.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = backup_home / (stamp + "-" + uuid.uuid4().hex[:8])
            destination.rename(backup)
        try:
            stage.rename(destination)
        except OSError:
            if backup is not None and not destination.exists():
                backup.rename(destination)
                backup = None
            raise
        if backup is not None:
            result["backup"] = str(backup)
        return result
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent / "glitch-idea")
    parser.add_argument("--skills-root", type=Path, default=Path.home() / ".codex" / "skills")
    parser.add_argument("--store", help="Durable idea store; relative paths resolve against the current directory")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = install(args.source, args.skills_root, args.store, args.dry_run)
    except (InstallError, OSError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "install_error", "message": str(exc)}}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
