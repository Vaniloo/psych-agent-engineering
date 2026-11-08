"""Download pinned official datasets. No teacher, generated examples or model training."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

SOURCES = (
    {"key": "toolace", "repository": "Team-ACE/ToolACE",
     "revision": "6bda777c88d21e5a204703c1ee45597a8fa4f734",
     "dataFile": "data.json", "license": "apache-2.0"},
    {"key": "apigen_mt", "repository": "Salesforce/APIGen-MT-5k",
     "revision": "abc4a517d67c541f85f6470cbd8fd3186b36830e",
     "dataFile": "apigen-mt_5k.json", "license": "cc-by-nc-4.0"},
)


def sha256_file(path):
    """Stream file checksums; shared by downloads, courses and checkpoints."""
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def download(url, destination, max_bytes=256 * 1024**2):
    if destination.exists():
        raise ValueError("refuse_overwriting_source_file")
    temp = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, timeout=90) as response, temp.open("xb") as f:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("source_file_exceeds_download_limit")
            digest.update(chunk)
            f.write(chunk)
    temp.replace(destination)
    destination.chmod(0o444)
    return {"sha256": digest.hexdigest(), "bytes": size, "url": url}


def fetch(out):
    out.mkdir(parents=True, exist_ok=False)
    index = {"status": "downloading", "sources": [], "syntheticGenerationEnabled": False,
             "teacherApiCalled": False, "trainingStarted": False}
    write_json(out / "manifest.json", index)
    try:
        for source in SOURCES:
            directory = out / "raw" / source["key"]
            directory.mkdir(parents=True)
            # Metadata is pinned to the same revision as the files.
            url = f"https://huggingface.co/api/datasets/{source['repository']}/revision/{source['revision']}"
            with urllib.request.urlopen(url, timeout=30) as response:
                metadata = json.load(response)
            if metadata["sha"] != source["revision"] or metadata.get("gated"):
                raise ValueError("source_revision_or_access_mismatch")
            if metadata.get("cardData", {}).get("license") != source["license"]:
                raise ValueError("source_license_changed_review_required")
            write_json(directory / "repository-metadata.json", metadata)
            files = {}
            for name in ["README.md", source["dataFile"]]:
                url = f"https://huggingface.co/datasets/{source['repository']}/resolve/{source['revision']}/{name}"
                item = download(url, directory / name)
                files[str((directory / name).relative_to(out))] = item
            index["sources"].append({**source, "files": files})
            write_json(out / "manifest.json", index)
            print(json.dumps({"downloaded": source["repository"], "revision": source["revision"],
                              "bytes": sum(v["bytes"] for v in files.values())}), flush=True)
        index["status"] = "download_complete"
        write_json(out / "manifest.json", index)
    except Exception as exc:
        index.update(status="download_failed", errorType=type(exc).__name__)
        write_json(out / "manifest.json", index)
        raise
    return index


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["status"] != "download_complete":
        raise ValueError("official_download_not_complete")
    if [{k: row[k] for k in SOURCES[0]} for row in manifest["sources"]] != list(SOURCES):
        raise ValueError("official_source_list_mismatch")
    for source in manifest["sources"]:
        required = {str(Path("raw") / source["key"] / name)
                    for name in ("README.md", source["dataFile"])}
        if set(source["files"]) != required:
            raise ValueError("official_source_files_mismatch")
        for relative, expected in source["files"].items():
            p = Path(relative)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError("invalid_raw_source_path")
            if sha256_file(root / p) != expected["sha256"] or (root / p).stat().st_size != expected["bytes"]:
                raise ValueError("official_source_checksum_mismatch")
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--verify-only", action="store_true")
    a = p.parse_args()
    result = verify(a.out) if a.verify_only else fetch(a.out)
    print(json.dumps({"status": result["status"], "sources": len(result["sources"])}))


if __name__ == "__main__":
    main()
