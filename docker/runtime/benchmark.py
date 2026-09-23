from __future__ import annotations

import ctypes
import hashlib
import importlib
import json
import os
import resource
import sysconfig
import time
import zlib


class LibraryInfo(ctypes.Structure):
    filename: bytes
    _fields_ = [
        ("filename", ctypes.c_char_p),
        ("base", ctypes.c_void_p),
        ("symbol", ctypes.c_char_p),
        ("address", ctypes.c_void_p),
    ]


library = ctypes.CDLL(None)
library.dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(LibraryInfo)]
library.dladdr.restype = ctypes.c_int
info = LibraryInfo()
assert library.dladdr(ctypes.cast(library.malloc, ctypes.c_void_p), ctypes.byref(info)) == 1
malloc_provider = info.filename.decode("utf-8")
if expected := os.environ.get("EXPECTED_MALLOC_PROVIDER"):
    assert malloc_provider == expected

started = time.perf_counter_ns()
importlib.import_module("ci_coordinator.api.http.app")
adapter_type = importlib.import_module("pydantic").TypeAdapter
import_ns = time.perf_counter_ns() - started

adapter = adapter_type(list[dict[str, int]])
value = [{"id": n, "elapsed": n * 17, "attempt": 1} for n in range(256)]
payload = json.dumps(value).encode()
compressed = zlib.compress(payload)
assert adapter.validate_json(payload) == value
assert zlib.decompress(compressed) == payload
timings: dict[str, int] = {}
cpu_timings: dict[str, int] = {}
for operation, action in (
    ("boundary_validation", lambda: adapter.validate_json(payload)),
    ("compression", lambda: zlib.compress(payload)),
    ("decompression", lambda: zlib.decompress(compressed)),
):
    started = time.perf_counter_ns()
    cpu_started = time.process_time_ns()
    for _ in range(2000):
        action()
    timings[operation] = time.perf_counter_ns() - started
    cpu_timings[operation] = time.process_time_ns() - cpu_started
print(
    json.dumps(
        {
            "importNs": import_ns,
            "elapsedNs": timings,
            "cpuNs": cpu_timings,
            "pythonConfigureArgs": sysconfig.get_config_var("CONFIG_ARGS"),
            "pythonCFlags": sysconfig.get_config_var("CFLAGS"),
            "zlibVersion": zlib.ZLIB_RUNTIME_VERSION,
            "mallocProvider": malloc_provider,
            "maxRssKiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "iterations": 2000,
            "payloadSha256": hashlib.sha256(payload).hexdigest(),
            "nonClaim": "Bounded import, Pydantic and zlib comparison; "
            "not connected service throughput or capacity",
        }
    )
)
