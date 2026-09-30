from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from scripts import release_repair_evidence as collector
from scripts.release_publisher_identity import IMAGE

LAYER = "sha256:" + "c" * 64


def _fixture(archive: Path, *, modern: bool) -> tuple[dict[str, object], str, dict[str, bytes]]:
    raw = (
        b'{ "architecture":"amd64", "os":"linux", "rootfs":{"type":"layers","diff_ids":["'
        + LAYER.encode()
        + b'"]}}'
    )
    digest = hashlib.sha256(raw).hexdigest()
    config_path = "blobs/sha256/" + digest if modern else digest + ".json"
    selected = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:" + digest},
        }
    ).encode()
    selector = "sha256:" + (hashlib.sha256(selected).hexdigest() if modern else digest)
    entries = {
        "manifest.json": json.dumps([{"Config": config_path}]).encode(),
        config_path: raw,
    }
    if modern:
        entries["blobs/sha256/" + selector[7:]] = selected
    _write(archive, entries)
    return (
        {"Id": selector, "RootFS": {"Type": "layers", "Layers": [LAYER]}},
        "sha256:" + digest,
        entries,
    )


def _write(archive: Path, entries: dict[str, bytes], *, symlink: str | None = None) -> None:
    with tarfile.open(archive, "w") as bundle:
        for name, value in entries.items():
            member = tarfile.TarInfo(name)
            if name == symlink:
                member.type = tarfile.SYMTYPE
                member.linkname = "foreign"
                bundle.addfile(member)
            else:
                member.size = len(value)
                bundle.addfile(member, io.BytesIO(value))


@pytest.mark.parametrize("modern", [False, True], ids=["config-selector", "manifest-selector"])
def test_original_config_bytes_bind_both_docker_namespaces(tmp_path: Path, modern: bool) -> None:
    archive = tmp_path / "image.tar"
    image, expected, _ = _fixture(archive, modern=modern)
    assert collector.image_config_digest(archive, image) == expected
    assert (image["Id"] == expected) is not modern


@pytest.mark.parametrize(
    "mutation",
    [
        "config-hash",
        "reference",
        "multiple",
        "symlink",
        "platform",
        "rootfs",
        "manifest-hash",
        "manifest-config",
        "index",
    ],
)
def test_archive_identity_rejects_independent_component_drift(
    tmp_path: Path, mutation: str
) -> None:
    archive = tmp_path / "image.tar"
    image, _, entries = _fixture(archive, modern=True)
    manifest = json.loads(entries["manifest.json"])
    config_path = manifest[0]["Config"]
    selected_path = "blobs/sha256/" + str(image["Id"])[7:]
    if mutation == "config-hash":
        entries[config_path] += b" "
    elif mutation == "reference":
        forbidden = "../" + config_path.rsplit("/", 1)[-1] + ".json"
        entries[forbidden] = entries[config_path]
        manifest[0]["Config"] = forbidden
    elif mutation == "multiple":
        manifest.append(manifest[0])
    elif mutation == "platform":
        config = json.loads(entries[config_path])
        config["architecture"] = "arm64"
        raw = json.dumps(config).encode()
        config_path = "blobs/sha256/" + hashlib.sha256(raw).hexdigest()
        entries[config_path] = raw
        manifest[0]["Config"] = config_path
        selected = json.loads(entries[selected_path])
        selected["config"]["digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
        selected_raw = json.dumps(selected).encode()
        image["Id"] = "sha256:" + hashlib.sha256(selected_raw).hexdigest()
        entries["blobs/sha256/" + str(image["Id"])[7:]] = selected_raw
    elif mutation == "rootfs":
        image["RootFS"] = {"Type": "layers", "Layers": ["sha256:" + "d" * 64]}
    elif mutation == "manifest-hash":
        entries[selected_path] += b" "
    elif mutation in {"manifest-config", "index"}:
        selected = json.loads(entries[selected_path])
        if mutation == "manifest-config":
            selected["config"]["digest"] = "sha256:" + "d" * 64
        else:
            selected["mediaType"] = "application/vnd.oci.image.index.v1+json"
        raw = json.dumps(selected).encode()
        image["Id"] = "sha256:" + hashlib.sha256(raw).hexdigest()
        entries["blobs/sha256/" + str(image["Id"])[7:]] = raw
    entries["manifest.json"] = json.dumps(manifest).encode()
    _write(archive, entries, symlink=config_path if mutation == "symlink" else None)
    reason = {
        "config-hash": "image config content digest",
        "reference": "image archive config reference",
        "multiple": "image archive requires one image",
        "symlink": "image archive JSON identity",
        "platform": "image config platform",
        "rootfs": "image config filesystem",
        "manifest-hash": "image manifest config binding",
        "manifest-config": "image manifest config binding",
        "index": "image manifest config binding",
    }[mutation]
    with pytest.raises(ValueError, match="^" + reason + "$"):
        collector.image_config_digest(archive, image)


@pytest.mark.parametrize("budget", ["archive", "members", "json"])
def test_identity_budgets_reject_before_unbounded_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    archive = tmp_path / "image.tar"
    image, _, _ = _fixture(archive, modern=True)
    monkeypatch.setattr(
        collector,
        {
            "archive": "MAX_IMAGE_ARCHIVE_BYTES",
            "members": "MAX_IMAGE_ARCHIVE_MEMBERS",
            "json": "MAX_IMAGE_JSON_BYTES",
        }[budget],
        1,
    )
    with pytest.raises(ValueError):
        collector.image_config_digest(archive, image)


def test_duplicate_metadata_and_nonregular_config_are_not_identity(tmp_path: Path) -> None:
    archive = tmp_path / "image.tar"
    image, _, entries = _fixture(archive, modern=True)
    _write(archive, entries)
    with tarfile.open(archive, "a") as bundle:
        duplicate = tarfile.TarInfo("manifest.json")
        duplicate.size = len(entries["manifest.json"])
        bundle.addfile(duplicate, io.BytesIO(entries["manifest.json"]))
    with pytest.raises(ValueError, match="member identity"):
        collector.image_config_digest(archive, image)


@pytest.mark.parametrize("failure", [False, True], ids=["complete", "save-failed"])
@pytest.mark.parametrize("registry", [False, True], ids=["local-manifest", "registry-index"])
def test_owned_save_uses_the_exact_selector_and_cleans_private_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool, registry: bool
) -> None:
    image, expected, entries = _fixture(tmp_path / "fixture.tar", modern=True)
    subject = IMAGE + "@sha256:" + "f" * 64 if registry else str(image["Id"])
    image["RepoDigests"] = [subject] if registry else []
    observed: list[Path] = []

    def save(*arguments: str, **options: object) -> bytes:
        assert arguments[:5] == ("image", "save", "--platform", "linux/amd64", "--output")
        assert arguments[6:] == (subject,)
        assert not options
        archive = Path(arguments[5])
        observed.append(archive)
        _write(archive, entries)
        if failure:
            raise ValueError("save failed")
        return b""

    monkeypatch.setattr(collector, "command", save)
    if failure:
        with pytest.raises(ValueError, match="save failed"):
            collector.docker_image_config_digest(image, subject=subject)
    else:
        assert collector.docker_image_config_digest(image, subject=subject) == expected
    assert len(observed) == 1 and not observed[0].parent.exists()
