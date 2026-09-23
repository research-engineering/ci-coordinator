"""Exercise captured-child flock inheritance with independent pipe barriers."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from scripts.bounded_process import spawn
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.lifecycle import instance_operation_lock
from scripts.dev_environment.private_files import bounded_private_lock, ensure_private_directory

_CHILD = """
import os, select, sys
lock_descriptor, ready_descriptor, release_descriptor = map(int, sys.argv[1:])
os.fstat(lock_descriptor)
print('owned-output', flush=True)
print('owned-error', file=sys.stderr, flush=True)
os.write(ready_descriptor, b'R')
ready, _, _ = select.select([release_descriptor], [], [], 10)
if ready:
    os.read(release_descriptor, 1)
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--ready-fd", type=int, required=True)
    parser.add_argument("--release-fd", type=int, required=True)
    parser.add_argument("--instance-root", type=Path)
    parser.add_argument("--state-home", type=Path)
    args = parser.parse_args()
    ensure_private_directory(args.lock.parent)
    with ExitStack() as owner:
        if args.instance_root is None:
            descriptor = owner.enter_context(bounded_private_lock(args.lock))
        else:
            identity = derive_instance_identity(args.instance_root, state_home=args.state_home)
            lease = owner.enter_context(instance_operation_lock(identity))
            descriptor = lease.inherited_fds[0]
        result = spawn(
            sys.executable,
            ("-S", "-c", _CHILD, str(descriptor), str(args.ready_fd), str(args.release_fd)),
            cwd=args.lock.parent,
            max_buffer=4096,
            timeout_seconds=15,
            inherited_fds=(descriptor, args.ready_fd, args.release_fd),
        )
    print(json.dumps(asdict(result)), flush=True)


if __name__ == "__main__":
    main()
