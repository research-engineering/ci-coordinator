from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from scripts.dependency_audit_reports import PackageIdentity
from scripts.proofkit_common import as_array, as_object, parse_json_object
from scripts.repository_source_admission import read_bounded_repository_text

PYTHON_PROJECTS = {
    "backend": ("ci-coordinator-backend", "editable"),
    "tooling/quality": ("ci-coordinator-quality-tools", "virtual"),
}
_NPM_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")


def audit_source(root: Path, path: str) -> str:
    return read_bounded_repository_text(root, PurePosixPath(path), maximum_bytes=16 * 1024 * 1024)


def python_inventory(root: Path, project: str) -> frozenset[PackageIdentity]:
    if project not in PYTHON_PROJECTS:
        raise ValueError("unowned Python audit project")
    name, local_kind = PYTHON_PROJECTS[project]
    metadata = tomllib.loads(audit_source(root, f"{project}/pyproject.toml"))
    if as_object(metadata.get("project"), "Python project").get("name") != name:
        raise ValueError("Python audit project identity changed")
    lock = tomllib.loads(audit_source(root, f"{project}/uv.lock"))
    if lock.get("version") != 1 or lock.get("revision") != 3 or "manifest" in lock:
        raise ValueError("Python audit lock scope changed")
    packages: set[PackageIdentity] = set()
    local_roots = 0
    for raw in as_array(lock.get("package"), "Python audit packages"):
        item = as_object(raw, "Python audit package")
        package_name, version = item.get("name"), item.get("version")
        if (
            not isinstance(package_name, str)
            or not package_name
            or not isinstance(version, str)
            or not version
        ):
            raise ValueError("Python audit package identity is missing")
        source = as_object(item.get("source"), "Python audit package source")
        if package_name == name and source == {local_kind: "."}:
            local_roots += 1
            continue
        if source != {"registry": "https://pypi.org/simple"}:
            raise ValueError("Python audit dependency has an unadmitted source")
        packages.add((package_name, version))
    if local_roots != 1 or not packages:
        raise ValueError(
            "Python audit requires one local project and nonempty registry dependencies"
        )
    return frozenset(packages)


def npm_lock_documents(content: str) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    try:
        yaml = YAML(typ="safe", pure=True)
        yaml.allow_duplicate_keys = False
        for document in yaml.load_all(content):
            if len(documents) == 2:
                raise ValueError("npm-lock-document-limit")
            if not isinstance(document, dict) or document.get("lockfileVersion") != "9.0":
                raise ValueError("npm-lock-document-shape")
            for key in ("importers", "packages", "snapshots"):
                if not isinstance(document.get(key), dict) or not document[key]:
                    raise ValueError("empty-npm-lock-document")
            documents.append(document)
    except (YAMLError, RecursionError) as error:
        raise ValueError("invalid-npm-lock-stream") from error
    if not documents:
        raise ValueError("empty-npm-lock")
    if len(documents) == 2:
        bootstrap = documents[0]["importers"]
        if (
            not isinstance(bootstrap, dict)
            or set(bootstrap) != {"."}
            or not isinstance(bootstrap["."], dict)
            or not any(
                isinstance(bootstrap["."].get(key), dict) and bootstrap["."][key]
                for key in ("configDependencies", "packageManagerDependencies")
            )
        ):
            raise ValueError("npm-lock-bootstrap-document")
    projects = documents[-1]["importers"]
    if (
        not isinstance(projects, dict)
        or not all(isinstance(importer, dict) for importer in projects.values())
        or not any(
            isinstance(importer.get(key), dict) and importer[key]
            for importer in projects.values()
            for key in ("dependencies", "devDependencies", "optionalDependencies")
        )
    ):
        raise ValueError("npm-lock-project-document-missing")

    return documents


def admit_npm_lock(content: str) -> None:
    npm_lock_documents(content)


def npm_inventory(root: Path) -> frozenset[PackageIdentity]:
    packages: set[PackageIdentity] = set()
    documents = npm_lock_documents(audit_source(root, "pnpm-lock.yaml"))
    for document in documents:
        rows = as_object(document["packages"], "npm packages")
        for key, raw in rows.items():
            name, separator, version = key.rpartition("@")
            item = as_object(raw, "npm package")
            resolution = as_object(item.get("resolution"), "npm package resolution")
            integrity = resolution.get("integrity")
            if (
                not separator
                or not name
                or _NPM_VERSION.fullmatch(version) is None
                or set(resolution) != {"integrity"}
                or not isinstance(integrity, str)
                or not integrity.startswith("sha512-")
            ):
                raise ValueError("npm audit dependency has an unadmitted source or version")
            packages.add((name, version))
        importers = as_object(document["importers"], "npm importers")
        for raw_importer in importers.values():
            importer = as_object(raw_importer, "npm importer")
            for field in (
                "dependencies",
                "devDependencies",
                "optionalDependencies",
                "configDependencies",
                "packageManagerDependencies",
            ):
                for raw in as_object(importer.get(field, {}), field).values():
                    reference = as_object(raw, "npm importer dependency").get("version")
                    if (
                        not isinstance(reference, str)
                        or not reference
                        or reference.startswith(
                            ("link:", "file:", "git", "http", "workspace:", "portal:")
                        )
                    ):
                        raise ValueError("npm audit importer has an unadmitted dependency source")
    if not packages:
        raise ValueError("npm audit inventory is empty")
    return frozenset(packages)


def admit_audit_configuration(root: Path) -> None:
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_duplicate_keys = False
    workspace = as_object(yaml.load(audit_source(root, "pnpm-workspace.yaml")), "npm workspace")
    if any(
        key in workspace
        for key in ("audit", "auditConfig", "auditLevel", "ignoreRegistryErrors", "ignoreUnfixable")
    ):
        raise ValueError("npm audit configuration requires explicit owner admission")
    for path in ("package.json", "frontend/package.json"):
        package = parse_json_object(audit_source(root, path), path)
        pnpm = as_object(package.get("pnpm", {}), "package pnpm configuration")
        if any("audit" in key.lower() or "ignore" in key.lower() for key in pnpm):
            raise ValueError("package-level audit suppression is not admitted")
    if audit_source(root, ".npmrc").strip() != "engine-strict=true":
        raise ValueError("npm audit repository rc configuration requires owner admission")
