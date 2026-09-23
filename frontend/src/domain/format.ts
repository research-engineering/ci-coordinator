const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  const seconds = String(date.getUTCSeconds()).padStart(2, "0");
  return `${day} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}, ${hours}:${minutes}:${seconds} UTC`;
}

export function shortIdentity(value: string): string {
  return value.length <= 16 ? value : `${value.slice(0, 8)}..${value.slice(-6)}`;
}

export function formatInteger(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatCount(value: number, singular: string): string {
  return `${formatInteger(value)} ${singular}${value === 1 ? "" : "s"}`;
}

export function formatPolicySource(source: string): string {
  try {
    const value: unknown = JSON.parse(
      source,
      (_key: string, value: unknown, context?: { readonly source?: string }) => {
        if (typeof value === "number" && String(value) !== context?.source) {
          throw new Error("Preserve original numeric spelling");
        }
        return value;
      },
    );
    return JSON.stringify(value, null, 2);
  } catch {
    return source;
  }
}
