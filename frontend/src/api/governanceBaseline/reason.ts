const MAX_REASON_BYTES = 1_024;

export function governanceBaselineReasonIsAdmitted(value: string): boolean {
  const codePoints = Array.from(value, (character) => character.codePointAt(0));
  const first = codePoints[0];
  const last = codePoints.at(-1);
  return (
    first !== undefined &&
    last !== undefined &&
    codePoints.every(
      (codePoint) =>
        codePoint !== undefined &&
        codePoint > 0x001f &&
        !(codePoint >= 0x007f && codePoint <= 0x009f) &&
        !(codePoint >= 0xd800 && codePoint <= 0xdfff),
    ) &&
    !isNoncanonicalBoundary(first) &&
    !isNoncanonicalBoundary(last) &&
    new TextEncoder().encode(value).byteLength <= MAX_REASON_BYTES
  );
}

function isNoncanonicalBoundary(codePoint: number): boolean {
  return (
    codePoint === 0x0020 ||
    codePoint === 0x00a0 ||
    codePoint === 0x1680 ||
    (codePoint >= 0x2000 && codePoint <= 0x200a) ||
    codePoint === 0x2028 ||
    codePoint === 0x2029 ||
    codePoint === 0x202f ||
    codePoint === 0x205f ||
    codePoint === 0x3000 ||
    codePoint === 0xfeff
  );
}
