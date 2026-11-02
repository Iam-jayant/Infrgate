/**
 * Canonicalize a JavaScript object into a deterministic JSON string.
 * Recursively sorts object keys to ensure consistent hashing regardless of key order.
 * 
 * @param obj - Arbitrary JSON-serializable value
 * @returns Canonical JSON string
 * 
 * @example
 * ```typescript
 * canonicalize({ b: 2, a: 1 }) === canonicalize({ a: 1, b: 2 })
 * // Both produce: '{"a":1,"b":2}'
 * ```
 */
export function canonicalize(obj: any): string {
  if (obj === null) {
    return 'null';
  }

  if (typeof obj !== 'object') {
    return JSON.stringify(obj);
  }

  if (Array.isArray(obj)) {
    return '[' + obj.map(canonicalize).join(',') + ']';
  }

  // Sort object keys for deterministic serialization
  const sortedKeys = Object.keys(obj).sort();
  const pairs = sortedKeys.map(key => {
    const value = canonicalize(obj[key]);
    return `"${key}":${value}`;
  });

  return '{' + pairs.join(',') + '}';
}
