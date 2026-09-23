from __future__ import annotations

import hashlib
import importlib.metadata
import os
import shutil
import stat
from pathlib import Path
from typing import cast


def signature(path: Path) -> tuple[int, str]:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        value = os.readlink(path)
    elif stat.S_ISREG(mode):
        value = hashlib.sha256(path.read_bytes()).hexdigest()
    elif stat.S_ISDIR(mode):
        value = ""
    else:
        raise ValueError(f"Unsupported installed entry: {path}")
    return mode, value


baseline = Path("/dependency-venv")
venv = Path("/app/backend/.venv")
application = Path("/application-layer/app/backend")
existing = {path.relative_to(baseline): signature(path) for path in baseline.rglob("*")}
for relative, expected in existing.items():
    if signature(venv / relative) != expected:
        raise ValueError(f"Dependency mutated: {relative}")

distribution = importlib.metadata.distribution("ci-coordinator-backend")
if not distribution.files:
    raise ValueError("Project installation has no file inventory")
# The image installs this distribution on the venv filesystem, not in a zip.
owned = {
    Path(cast(Path, distribution.locate_file(file))).absolute().resolve()
    for file in distribution.files
}
for path in venv.rglob("*"):
    relative = path.relative_to(venv)
    if relative in existing or path.is_dir():
        continue
    if path.resolve() not in owned:
        raise ValueError(f"Unowned new installation file: {relative}")
    target = application / ".venv" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target, follow_symlinks=False)

for directory in ("src/ci_coordinator", "alembic"):
    shutil.copytree(Path("/app/backend") / directory, application / directory)
for name in ("alembic.ini", "pyproject.toml"):
    shutil.copy2(Path("/app/backend") / name, application / name)
