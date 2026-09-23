const MAX_OPERATION_ID_BYTES = 256;

export function operationIdIsAdmitted(value: string): boolean {
  return (
    value.length > 0 &&
    !value.includes("\0") &&
    isUnicodeScalarText(value) &&
    new TextEncoder().encode(value).byteLength <= MAX_OPERATION_ID_BYTES
  );
}

function isUnicodeScalarText(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const trail = value.charCodeAt(index + 1);
      if (!(trail >= 0xdc00 && trail <= 0xdfff)) return false;
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
  }
  return true;
}
