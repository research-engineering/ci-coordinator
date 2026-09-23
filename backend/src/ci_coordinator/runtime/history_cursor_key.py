from ci_coordinator.runtime.cursor_key import derive_runtime_cursor_key


def derive_history_cursor_key(private_key_pem: str) -> bytes:
    return derive_runtime_cursor_key(private_key_pem, purpose="history")
