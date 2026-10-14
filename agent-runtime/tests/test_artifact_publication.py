import hashlib
import io
import tarfile

import pytest

from training.fetch_artifacts import download_asset, install_archive, verify


def archive(tmp_path, members):
    target = tmp_path / "release.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for name, blob in members:
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return target


def entry(blob):
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}


def test_release_install_checks_all_content_and_keeps_existing_metadata(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    name = "models/test/weights.safetensors"
    content = b"example model bytes"
    files = {name: entry(content)}
    asset = archive(tmp_path, [(name, content)])
    install_archive(asset, root, files)
    install_archive(asset, root, files)
    verify(root, files)
    (root / name).write_bytes(b"modified")
    with pytest.raises(ValueError, match="overwrite"):
        install_archive(asset, root, files)


def test_bad_archive_never_installs_partial_artifacts(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    names = ["models/test/a", "models/test/b"]
    files = {n: entry(b"good") for n in names}
    asset = archive(tmp_path, [(names[0], b"good"), (names[1], b"evil")])
    with pytest.raises(ValueError, match="checksum"):
        install_archive(asset, root, files)
    assert not list(root.rglob("*"))


@pytest.mark.parametrize("name", ["../outside", "/absolute", "unlisted"])
def test_archive_rejects_unknown_or_escaping_members(tmp_path, name):
    asset = archive(tmp_path, [(name, b"data")])
    with pytest.raises(ValueError, match="member"):
        install_archive(asset, tmp_path / "root", {"models/test/a": entry(b"data")})


def test_private_assets_use_existing_cli_authorization(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("training.fetch_artifacts.shutil.which", lambda name: "/tools/gh")
    monkeypatch.setattr("training.fetch_artifacts.subprocess.run",
                        lambda arguments, **kwargs: calls.append((arguments, kwargs)))
    path = download_asset({"name": "model.tar.gz"}, {
        "repository": "owner/private", "releaseTag": "models-test",
        "repositoryVisibility": "PRIVATE",
    }, tmp_path)
    assert path == tmp_path / "model.tar.gz"
    assert calls == [(["/tools/gh", "release", "download", "models-test", "--repo",
                      "owner/private", "--pattern", "model.tar.gz", "--dir", str(tmp_path)],
                     {"check": True})]


def test_private_assets_without_cli_do_not_try_anonymous_download(monkeypatch, tmp_path):
    monkeypatch.setattr("training.fetch_artifacts.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="gh auth login"):
        download_asset({"name": "model.tar.gz"}, {"repositoryVisibility": "PRIVATE"}, tmp_path)
