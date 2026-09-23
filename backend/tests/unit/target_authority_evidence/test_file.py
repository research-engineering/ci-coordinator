from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from ci_coordinator.target_authority_evidence import (
    TargetAuthorityEvidenceError,
    encode_target_authority_evidence,
    publish_target_authority_evidence,
    read_target_authority_evidence,
)
from ci_coordinator.target_authority_evidence.cli import main

from .factories import EvidenceFixture


def test_verified_bundle_is_published_once_as_private_content_addressed_file(
    fixture: EvidenceFixture,
    tmp_path: Path,
) -> None:
    source = tmp_path / "evidence.json"
    source.write_bytes(encode_target_authority_evidence(fixture.admitted))
    destination = _private_directory(tmp_path / "published")

    loaded = read_target_authority_evidence(source)
    published = publish_target_authority_evidence(loaded, destination)

    assert published.path.name == f"{fixture.admitted.bundle.bundle_digest}.json"
    assert published.path.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(published.path.stat().st_mode) == 0o600
    with pytest.raises(TargetAuthorityEvidenceError) as collision:
        publish_target_authority_evidence(loaded, destination)
    assert collision.value.code == "output_collision"


def test_input_and_destination_symlinks_are_rejected(
    fixture: EvidenceFixture, tmp_path: Path
) -> None:
    source = tmp_path / "evidence.json"
    source.write_bytes(encode_target_authority_evidence(fixture.admitted))
    source_link = tmp_path / "evidence-link.json"
    source_link.symlink_to(source)

    with pytest.raises(TargetAuthorityEvidenceError) as input_error:
        read_target_authority_evidence(source_link)
    assert input_error.value.code == "input_open_failed"

    destination = _private_directory(tmp_path / "real-destination")
    destination_link = tmp_path / "destination-link"
    destination_link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(TargetAuthorityEvidenceError) as output_error:
        publish_target_authority_evidence(fixture.admitted, destination_link)
    assert output_error.value.code == "output_open_failed"


def test_group_writable_destination_is_rejected(fixture: EvidenceFixture, tmp_path: Path) -> None:
    destination = tmp_path / "shared"
    destination.mkdir(mode=0o770)
    destination.chmod(0o770)

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        publish_target_authority_evidence(fixture.admitted, destination)

    assert raised.value.code == "output_ownership_rejected"


def test_non_regular_input_is_rejected(tmp_path: Path) -> None:
    source = _private_directory(tmp_path / "input-directory")

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        read_target_authority_evidence(source)

    assert raised.value.code == "input_not_regular"


def test_input_identity_change_during_read_is_rejected(
    fixture: EvidenceFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "evidence.json"
    source.write_bytes(encode_target_authority_evidence(fixture.admitted))
    original_fstat = os.fstat
    calls = 0

    def changed_second_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        value = original_fstat(descriptor)
        calls += 1
        if calls != 2:
            return value
        fields = list(value)
        fields[8] = value.st_mtime + 1
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", changed_second_fstat)

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        read_target_authority_evidence(source)

    assert raised.value.code == "input_changed"


@pytest.mark.parametrize(
    "failure",
    ("fchmod", "write", "fsync", "link", "unsupported_link"),
)
def test_publication_faults_leave_no_final_artifact(
    fixture: EvidenceFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    destination = _private_directory(tmp_path / failure)
    staged_descriptor: int | None = None

    if failure == "fchmod":

        def fail_fchmod(descriptor: int, _mode: int) -> None:
            nonlocal staged_descriptor
            staged_descriptor = descriptor
            raise OSError("injected fchmod failure")

        monkeypatch.setattr(os, "fchmod", fail_fchmod)
    elif failure == "write":
        monkeypatch.setattr(os, "write", lambda *_args: 0)
    elif failure == "fsync":

        def fail_fsync(_descriptor: int) -> None:
            raise OSError("injected fsync failure")

        monkeypatch.setattr(os, "fsync", fail_fsync)
    elif failure == "link":

        def fail_link(*_args: object, **_kwargs: object) -> None:
            raise OSError("injected link failure")

        monkeypatch.setattr(os, "link", fail_link)
    else:

        def unsupported_link(*_args: object, **_kwargs: object) -> None:
            raise NotImplementedError("injected unsupported link semantics")

        monkeypatch.setattr(os, "link", unsupported_link)

    with pytest.raises(TargetAuthorityEvidenceError):
        publish_target_authority_evidence(fixture.admitted, destination)

    assert not tuple(destination.glob("*.json"))
    assert not tuple(destination.glob("*.tmp"))
    if staged_descriptor is not None:
        with pytest.raises(OSError):
            os.fstat(staged_descriptor)


@pytest.mark.parametrize("failure", ("post_link_fsync", "temporary_unlink", "final_fsync"))
def test_post_link_faults_return_no_affirmative_receipt(
    fixture: EvidenceFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    destination = _private_directory(tmp_path / failure)
    content = encode_target_authority_evidence(fixture.admitted)
    original_fsync = os.fsync
    original_unlink = os.unlink
    fsync_calls = 0

    if failure in {"post_link_fsync", "final_fsync"}:

        def fail_selected_fsync(descriptor: int) -> None:
            nonlocal fsync_calls
            fsync_calls += 1
            selected_call = 2 if failure == "post_link_fsync" else 3
            if fsync_calls == selected_call:
                raise OSError("injected directory fsync failure")
            original_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fail_selected_fsync)
    else:

        def fail_unlink(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("injected temporary unlink failure")

        monkeypatch.setattr(os, "unlink", fail_unlink)

    with pytest.raises(TargetAuthorityEvidenceError):
        publish_target_authority_evidence(fixture.admitted, destination)

    finals = tuple(destination.glob("*.json"))
    assert len(finals) == 1
    assert finals[0].read_bytes() == content
    if failure != "temporary_unlink":
        assert not tuple(destination.glob("*.tmp"))
    monkeypatch.setattr(os, "unlink", original_unlink)


def test_cli_outputs_only_redacted_canonical_status(
    fixture: EvidenceFixture,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "evidence.json"
    source.write_bytes(encode_target_authority_evidence(fixture.admitted))
    destination = _private_directory(tmp_path / "cli-output")

    assert main(["--input", str(source), "--output-directory", str(destination)]) == 0
    success = json.loads(capsys.readouterr().out)
    assert success == {
        "bundleDigest": fixture.admitted.bundle.bundle_digest,
        "code": "evidence_published",
        "path": str(destination / f"{fixture.admitted.bundle.bundle_digest}.json"),
        "status": "published",
    }

    assert main(["--input", str(source), "--output-directory", str(destination)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "output_collision",
        "status": "rejected",
    }


@pytest.mark.parametrize("arguments", ((), ("--unknown", "sensitive-argument")))
def test_cli_argument_rejection_is_redacted_canonical_json(
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
) -> None:
    assert main(arguments) == 2

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "code": "arguments_rejected",
        "status": "rejected",
    }
    assert captured.err == ""
    assert "sensitive-argument" not in captured.out


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    assert path.is_absolute()
    assert os.geteuid() == path.stat().st_uid
    return path
