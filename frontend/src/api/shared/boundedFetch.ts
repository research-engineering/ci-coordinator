import { captureResponseObservers } from "./responseObservation";

const DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
export const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;
export const MAX_REQUEST_TIMEOUT_MS = 2_147_483_647;

export class ResponseLimitError extends Error {}

export function combinedSignal(signal: AbortSignal | undefined, timeoutMs: number): AbortSignal {
  const admittedTimeout = admitRequestTimeout(timeoutMs);
  const timeoutSignal = AbortSignal.timeout(admittedTimeout);
  return signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
}

export function admitRequestTimeout(timeoutMs: number): number {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_REQUEST_TIMEOUT_MS) {
    throw new RangeError("request timeout must be an admitted positive integer");
  }
  return timeoutMs;
}

export async function boundedFetch(
  request: Request,
  maximumBytes = DEFAULT_MAX_RESPONSE_BYTES,
): Promise<Response> {
  if (
    !Number.isSafeInteger(maximumBytes) ||
    maximumBytes < 1 ||
    maximumBytes > MAX_RESPONSE_BYTES
  ) {
    throw new RangeError("maximum response bytes must be within the platform bound");
  }
  const notify = captureResponseObservers(request);
  const response = await globalThis.fetch(request);
  const declaredLength = admittedDecodedContentLength(response, maximumBytes);
  if (declaredLength === "invalid" || declaredLength === "oversized") {
    cancelStream(response.body);
    throw new ResponseLimitError("response exceeds the admitted byte bound");
  }
  if (!response.body) {
    if (typeof declaredLength === "number" && declaredLength !== 0) {
      throw new ResponseLimitError("response length does not match its body");
    }
    notify(response.status);
    return response;
  }

  const reader = response.body.getReader();
  const exactBody = typeof declaredLength === "number" ? new Uint8Array(declaredLength) : undefined;
  const chunks: Uint8Array[] = [];
  let size = 0;
  let complete = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        complete = true;
        break;
      }
      const nextSize = size + value.byteLength;
      if (nextSize > maximumBytes || (exactBody !== undefined && nextSize > exactBody.byteLength)) {
        throw new ResponseLimitError("response exceeds the admitted byte bound");
      }
      if (exactBody === undefined) chunks.push(value);
      else exactBody.set(value, size);
      size = nextSize;
    }
    if (exactBody !== undefined && size !== exactBody.byteLength) {
      throw new ResponseLimitError("response length does not match its body");
    }
  } finally {
    if (!complete) cancelReader(reader);
    releaseReader(reader);
  }
  const body = exactBody ?? concatenateChunks(chunks, size);
  const headers = admittedMaterializedHeaders(response.headers);
  const materialized = new Response(body.buffer, {
    headers,
    status: response.status,
    statusText: response.statusText,
  });
  notify(response.status);
  return materialized;
}

function admittedMaterializedHeaders(source: Headers): Headers {
  const headers = new Headers(source);
  if (headers.has("content-encoding")) {
    headers.delete("content-encoding");
    headers.delete("content-length");
  }
  return headers;
}

function admittedDecodedContentLength(
  response: Response,
  maximumBytes: number,
): number | "invalid" | "oversized" | undefined {
  if (response.headers.has("content-encoding")) return undefined;
  const supplied = response.headers.get("content-length");
  if (supplied === null) return undefined;
  if (!/^(?:0|[1-9][0-9]*)$/.test(supplied)) return "invalid";
  const parsed = Number(supplied);
  if (!Number.isSafeInteger(parsed)) return "oversized";
  return parsed > maximumBytes ? "oversized" : parsed;
}

function concatenateChunks(chunks: readonly Uint8Array[], size: number): Uint8Array<ArrayBuffer> {
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

function cancelStream(stream: ReadableStream<Uint8Array> | null): void {
  if (!stream) return;
  try {
    void stream.cancel().catch(() => undefined);
  } catch {
    // Cleanup failure must not replace the bounded-response classification.
  }
}

function cancelReader(reader: ReadableStreamDefaultReader<Uint8Array>): void {
  try {
    void reader.cancel().catch(() => undefined);
  } catch {
    // Cleanup failure must not replace the bounded-response classification.
  }
}

function releaseReader(reader: ReadableStreamDefaultReader<Uint8Array>): void {
  try {
    reader.releaseLock();
  } catch {
    // Cleanup failure must not replace the bounded-response classification.
  }
}
