/**
 * Error thrown when Web Crypto API is unavailable.
 * Requires Node.js 16+ or a modern browser.
 */
export class CryptoUnavailableError extends Error {
  constructor() {
    super(
      'Web Crypto API not available. ' +
      'Requires Node.js 16+ or modern browser (Chrome 90+, Firefox 88+, Safari 15+). ' +
      'For Node.js 15.x, import crypto.webcrypto explicitly.'
    );
    this.name = 'CryptoUnavailableError';
  }
}

/**
 * Get the crypto.subtle object, throwing if unavailable.
 * 
 * @returns SubtleCrypto instance
 * @throws {CryptoUnavailableError} If Web Crypto API is not available
 */
export function getSubtleCrypto(): SubtleCrypto {
  // Node.js 16+ / Browser / Edge runtimes
  if (typeof globalThis !== 'undefined' && globalThis.crypto?.subtle) {
    return globalThis.crypto.subtle;
  }

  // Node.js 15.x fallback (requires explicit import by user)
  if (typeof global !== 'undefined' && (global as any).crypto?.subtle) {
    return (global as any).crypto.subtle;
  }

  throw new CryptoUnavailableError();
}

/**
 * Convert ArrayBuffer to lowercase hex string.
 * 
 * @param buffer - Raw byte buffer
 * @returns Hex-encoded string
 */
function bufferToHex(buffer: ArrayBuffer): string {
  const byteArray = new Uint8Array(buffer);
  return Array.from(byteArray)
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Compute SHA-256 hash of input string using Web Crypto API.
 * 
 * @param input - Input string to hash
 * @returns Lowercase hex-encoded SHA-256 hash (64 characters)
 * @throws {CryptoUnavailableError} If Web Crypto API is not available
 * 
 * @example
 * ```typescript
 * const hash = await sha256('hello');
 * console.log(hash);
 * // "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
 * ```
 */
export async function sha256(input: string): Promise<string> {
  const subtle = getSubtleCrypto();
  const encoder = new TextEncoder();
  const data = encoder.encode(input);
  const hashBuffer = await subtle.digest('SHA-256', data);
  return bufferToHex(hashBuffer);
}
