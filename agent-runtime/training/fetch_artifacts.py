"""Download the published model assets into the same directory as the frozen courses."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(root, files, *, weights=True):
    root = Path(root)
    for name, entry in files.items():
        if not weights and name.endswith(".safetensors"):
            continue
        path = root / name
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise ValueError(f"missing or wrong-size artifact: {name}")
        if digest(path) != entry["sha256"]:
            raise ValueError(f"artifact checksum mismatch: {name}")


def install_archive(archive, root, files):
    """Verify every member before installing; never trust tar paths or linked files."""
    root = Path(root).resolve()
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        with tarfile.open(archive, "r:gz") as tar:
            seen = set()
            for member in tar.getmembers():
                name = member.name
                path = Path(name)
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or ".." in path.parts
                    or name not in files
                    or name in seen
                    or member.size != files[name]["bytes"]
                ):
                    raise ValueError("unexpected archive member")
                seen.add(name)
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            if seen != set(files):
                raise ValueError("incomplete model archive")
        verify(stage, files)
        # Check ALL destinations before writing, including symlinks and modified metadata.
        for name in files:
            destination = root / name
            if not destination.resolve().is_relative_to(root) or destination.is_symlink():
                raise ValueError("artifact destination escapes root")
            if destination.exists() and (
                not destination.is_file() or digest(destination) != files[name]["sha256"]
            ):
                raise ValueError(f"refuse to overwrite modified artifact: {name}")
        for name in files:
            destination = root / name
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(stage / name, destination)


def download_asset(asset, manifest, directory):
    """Use the user's GitHub CLI login for private repositories; never print tokens."""
    executable = shutil.which("gh")
    if executable:
        subprocess.run([
            executable, "release", "download", manifest["releaseTag"],
            "--repo", manifest.get("repository", "Vaniloo/psych-agent-engineering"),
            "--pattern", asset["name"], "--dir", str(directory),
        ], check=True)
        return Path(directory) / asset["name"]
    if manifest.get("repositoryVisibility") == "PRIVATE":
        raise RuntimeError("Private repository: install GitHub CLI and run gh auth login first.")
    archive = Path(directory) / asset["name"]
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "Psych-Agent"})
    with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--verify-data", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--models", nargs="+")
    args = parser.parse_args()
    if args.root is None:
        args.root = Path(__file__).parent / "artifacts/20261020"
        if not args.models:
            args.models = ["toolace-qwen4b-20261019"]
    manifest = json.loads((args.root / "manifest.json").read_text())
    if args.verify_data or args.verify_only:
        files = manifest["files"]
        if args.models:
            if any(name not in manifest["models"] for name in args.models):
                parser.error("unknown model")
            prefixes = tuple(f"models/{name}/" for name in args.models)
            files = {name: entry for name, entry in files.items() if name.startswith(prefixes)}
        verify(args.root, files, weights=not args.verify_data)
        print("Artifact checksums verified.")
        return
    names = args.models or manifest["models"]
    if any(name not in manifest["models"] for name in names):
        parser.error("unknown model")
    verify(args.root, manifest["files"], weights=False)
    for name in names:
        asset = manifest["assets"][name]
        files = {k: v for k, v in manifest["files"].items() if k.startswith(f"models/{name}/")}
        if all((args.root / p).is_file() and digest(args.root / p) == v["sha256"]
               for p, v in files.items()):
            print(f"Already verified: {name}")
            continue
        with tempfile.TemporaryDirectory() as temporary:
            archive = download_asset(asset, manifest, temporary)
            if archive.stat().st_size != asset["bytes"] or digest(archive) != asset["sha256"]:
                raise ValueError("release asset checksum mismatch")
            install_archive(archive, args.root, files)
        print(f"Installed and verified: {name}")


if __name__ == "__main__":
    main()
