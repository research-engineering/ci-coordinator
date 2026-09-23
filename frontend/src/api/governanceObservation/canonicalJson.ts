interface JsonResourceLimits {
  readonly maximumDepth: number;
  readonly maximumNodes: number;
}

interface EncodingState {
  readonly active: WeakSet<object>;
  readonly limits: JsonResourceLimits | undefined;
  nodes: number;
}

export function canonicalJsonText(value: unknown, limits?: JsonResourceLimits): string {
  const state: EncodingState = { active: new WeakSet(), limits, nodes: 0 };
  return encodeValue(value, 0, state);
}

function encodeValue(value: unknown, depth: number, state: EncodingState): string {
  state.nodes += 1;
  if (
    state.limits &&
    (depth > state.limits.maximumDepth || state.nodes > state.limits.maximumNodes)
  ) {
    throw new TypeError("JSON value exceeds its resource limits");
  }
  if (value === null || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "string") {
    requireUnicodeScalarText(value);
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) {
      throw new TypeError("number escaped the safe JSON domain");
    }
    return Object.is(value, -0) ? "0" : JSON.stringify(value);
  }
  if (typeof value !== "object") throw new TypeError("value escaped the JSON domain");
  if (state.active.has(value)) throw new TypeError("cyclic value escaped the JSON domain");

  state.active.add(value);
  try {
    if (Array.isArray(value)) {
      return `[${value.map((item) => encodeValue(item, depth + 1, state)).join(",")}]`;
    }
    const record = value as Record<string, unknown>;
    const entries = Object.keys(record)
      .sort(compareUnicodeScalars)
      .map((key) => {
        requireUnicodeScalarText(key);
        return `${JSON.stringify(key)}:${encodeValue(record[key], depth + 1, state)}`;
      });
    return `{${entries.join(",")}}`;
  } finally {
    state.active.delete(value);
  }
}

function compareUnicodeScalars(left: string, right: string): number {
  const leftScalars = [...left].map((character) => character.codePointAt(0) ?? 0);
  const rightScalars = [...right].map((character) => character.codePointAt(0) ?? 0);
  const sharedLength = Math.min(leftScalars.length, rightScalars.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference = (leftScalars[index] ?? 0) - (rightScalars[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return leftScalars.length - rightScalars.length;
}

function requireUnicodeScalarText(value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit < 0xd800 || codeUnit > 0xdfff) continue;
    if (
      codeUnit <= 0xdbff &&
      index + 1 < value.length &&
      value.charCodeAt(index + 1) >= 0xdc00 &&
      value.charCodeAt(index + 1) <= 0xdfff
    ) {
      index += 1;
      continue;
    }
    throw new TypeError("text contains an unpaired surrogate");
  }
}
