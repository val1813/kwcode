// Coercion utilities: convert string inputs to typed values.
// Bug: coerceNumber returns NaN for valid numeric strings due to wrong parsing

export function coerceNumber(value: unknown): number | undefined {
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    // Bug: uses parseInt which truncates decimals, and fails for "3.14"
    // Should use parseFloat or Number()
    const n = parseInt(value, 10);
    return isNaN(n) ? undefined : n;
  }
  return undefined;
}

export function coerceBoolean(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (value === "true" || value === "1" || value === 1) return true;
  if (value === "false" || value === "0" || value === 0) return false;
  return undefined;
}

export function coerceString(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function coerceArray<T>(
  value: unknown,
  itemCoercer: (v: unknown) => T | undefined
): T[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(itemCoercer)
    .filter((v): v is T => v !== undefined);
}
