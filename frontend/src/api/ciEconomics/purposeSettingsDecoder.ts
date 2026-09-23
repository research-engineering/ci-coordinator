import { visit } from "jsonc-parser";

export async function decodePurposeSettings(response: Response): Promise<unknown> {
  const bytes = await response.arrayBuffer();
  let source: string;
  try {
    source = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new SyntaxError("Invalid purpose settings encoding");
  }
  const objects: Set<string>[] = [];
  let depth = 0;
  let nodes = 0;
  function admitNode(): void {
    if (++nodes > 4096) throw new SyntaxError("Purpose settings node limit exceeded");
  }
  function beginContainer(): void {
    admitNode();
    if (++depth > 16) throw new SyntaxError("Purpose settings depth limit exceeded");
  }
  visit(
    source,
    {
      onObjectBegin() {
        beginContainer();
        objects.push(new Set());
      },
      onObjectProperty(key) {
        admitNode();
        const keys = objects.at(-1);
        if (!keys || keys.has(key)) throw new SyntaxError("Duplicate purpose settings key");
        keys.add(key);
      },
      onObjectEnd() {
        objects.pop();
        depth--;
      },
      onArrayBegin: beginContainer,
      onArrayEnd() {
        depth--;
      },
      onLiteralValue(value: unknown, offset, length) {
        admitNode();
        if (
          typeof value === "number" &&
          (!Number.isSafeInteger(value) ||
            !/^-?(?:0|[1-9][0-9]*)$/.test(source.slice(offset, offset + length)))
        ) {
          throw new SyntaxError("Purpose settings require exact integer tokens");
        }
      },
      onError() {
        throw new SyntaxError("Invalid purpose settings JSON");
      },
    },
    { disallowComments: true, allowTrailingComma: false, allowEmptyContent: false },
  );
  return JSON.parse(source) as unknown;
}
