/**
 * Correlation ID Generation
 *
 * Generates unique correlation IDs for command tracing.
 * Uses crypto.randomUUID() when available, falling back to
 * a timestamp-based identifier if unavailable.
 */

import { createLogger } from './logger';

const logger = createLogger('correlation');

/**
 * Generate a unique correlation ID.
 *
 * Prefers crypto.randomUUID() for RFC 4122 UUID v4 format.
 * Falls back to a timestamp-based ID (ts-{Date.now()}-{random4hex})
 * if crypto.randomUUID is unavailable, logging the fallback at WARN level.
 */
export function generateCorrelationId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }

  // Fallback: timestamp-based ID
  logger.warn('crypto.randomUUID unavailable, using timestamp-based fallback ID');
  const hex4 = Math.random().toString(16).slice(2, 6).padEnd(4, '0');
  return `ts-${Date.now()}-${hex4}`;
}
