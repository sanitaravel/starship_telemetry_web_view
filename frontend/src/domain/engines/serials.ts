/** Longest serial accepted; real ones look like "R576". */
export const MAX_SERIAL_LENGTH = 8;

/**
 * Cleans what was typed into an engine serial: upper case, letters, digits
 * and dashes only, at most MAX_SERIAL_LENGTH characters. An empty result
 * means "no serial".
 */
export function normaliseSerial(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/[^A-Z0-9-]/g, '')
    .slice(0, MAX_SERIAL_LENGTH);
}
