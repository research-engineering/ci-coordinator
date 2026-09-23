def bounded_text(value: str, maximum_bytes: int) -> bool:
    return (
        bool(value)
        and "\0" not in value
        and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        and len(value.encode("utf-8")) <= maximum_bytes
    )
