"""Fixture contract selection tests the protocol, not native Compose admission."""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.lifecycle import sigterm_guard
from scripts.dev_environment.watch_session import (
    WatchClientContract,
    WatchSession,
    owned_watch_session,
)


@contextmanager
def _parent_phase(phase: str | None, descriptor: int | None) -> Iterator[None]:
    def barrier(label: str) -> None:
        if descriptor is None:
            raise ValueError("parent phase requires a release descriptor")
        print(label, flush=True)
        ready, _, _ = select.select([descriptor], [], [], 5)
        if not ready or os.read(descriptor, 1) != b"1":
            raise RuntimeError("parent phase barrier was not released")

    with ExitStack() as stack:
        if phase == "supervised":
            stop_requested = WatchSession.stop_requested
            calls = 0

            def supervised(session: WatchSession) -> bool:
                nonlocal calls
                calls += 1
                # The first call is pre-spawn; the second is inside cleanup protection.
                if calls == 2:
                    barrier("PARENT_SUPERVISING")
                return stop_requested(session)

            stack.enter_context(patch.object(WatchSession, "stop_requested", supervised))
        elif phase == "transfer":
            popen = subprocess.Popen

            def transferring(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = popen(*args, **kwargs)
                # Native spawn succeeded, but run_interactive has not received its handle.
                barrier("PARENT_TRANSFERRING")
                return process

            stack.enter_context(patch.object(subprocess, "Popen", transferring))
        yield


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--state-home", type=Path, required=True)
    parser.add_argument("--joined-client-fixture", action="store_true")
    parser.add_argument("--parent-phase", choices=("supervised", "transfer"))
    parser.add_argument("--phase-release-fd", type=int)
    parser.add_argument("provider", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    identity = derive_instance_identity(args.repo_root, state_home=args.state_home)
    with (
        _parent_phase(args.parent_phase, args.phase_release_fd),
        sigterm_guard(),
        owned_watch_session(identity) as session,
    ):
        print(json.dumps({"publishedNonce": session.nonce}), flush=True)
        process = session.run(
            args.provider,
            cwd=identity.repo_root,
            env=os.environ,
            client_contract=(
                WatchClientContract.COMPOSE_JOINED_WATCH if args.joined_client_fixture else None
            ),
            timeout_seconds=20,
            graceful_seconds=0.2,
            kill_seconds=0.2,
        )
    print(json.dumps({"outcome": asdict(session.outcome), "process": asdict(process)}), flush=True)


if __name__ == "__main__":
    main()
