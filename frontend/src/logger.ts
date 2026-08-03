/**
 * Frontend Logger Utility
 *
 * Provides leveled logging with timestamp/module prefixing, configurable
 * severity filtering, and correlation ID attachment for command tracing.
 */

export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR';

const LEVEL_ORDER: Record<LogLevel, number> = {
  DEBUG: 0,
  INFO: 1,
  WARN: 2,
  ERROR: 3,
};

const CONSOLE_METHODS: Record<LogLevel, 'debug' | 'info' | 'warn' | 'error'> = {
  DEBUG: 'debug',
  INFO: 'info',
  WARN: 'warn',
  ERROR: 'error',
};

const VALID_LEVELS: ReadonlySet<string> = new Set(['DEBUG', 'INFO', 'WARN', 'ERROR']);

const MAX_MODULE_LENGTH = 64;

// --- Module-level state ---

let currentLevel: LogLevel = resolveInitialLevel();
let correlationId: string | null = null;

// --- Correlation ID management (for use by correlation.ts) ---

export function setCorrelationId(id: string | null): void {
  correlationId = id;
}

export function getCorrelationId(): string | null {
  return correlationId;
}

// --- Log level management ---

/**
 * Resolve the initial log level from available sources in priority order:
 * 1. localStorage 'LOG_LEVEL'
 * 2. import.meta.env.VITE_LOG_LEVEL
 * 3. default 'WARN'
 */
function resolveInitialLevel(): LogLevel {
  // Try localStorage first
  try {
    const stored = localStorage.getItem('LOG_LEVEL');
    if (stored && isValidLevel(stored)) {
      return normalizeLevel(stored);
    }
  } catch {
    // localStorage may throw in private browsing or SSR — fall through
  }

  // Try build-time env variable
  try {
    const envLevel = import.meta.env.VITE_LOG_LEVEL;
    if (envLevel && isValidLevel(envLevel)) {
      return normalizeLevel(envLevel);
    }
  } catch {
    // import.meta.env may not exist in some environments — fall through
  }

  // Default
  return 'WARN';
}

function isValidLevel(value: string): boolean {
  return VALID_LEVELS.has(value.toUpperCase());
}

function normalizeLevel(value: string): LogLevel {
  return value.toUpperCase() as LogLevel;
}

/**
 * Set the global log level. Rejects invalid values with a warning.
 */
export function setLogLevel(level: string): void {
  if (isValidLevel(level)) {
    currentLevel = normalizeLevel(level);
  } else {
    // Log warning about invalid level using current logger
    const logger = new Logger('logger');
    logger.warn(`Invalid log level rejected: "${level}". Retaining current level: ${currentLevel}`);
  }
}

/**
 * Get the current effective log level.
 */
export function getLogLevel(): LogLevel {
  return currentLevel;
}

// --- Logger class ---

export class Logger {
  private readonly module: string;

  constructor(module: string) {
    this.module = module.length > MAX_MODULE_LENGTH
      ? module.slice(0, MAX_MODULE_LENGTH)
      : module;
  }

  debug(message: string, context?: Record<string, unknown>): void {
    this.log('DEBUG', message, context);
  }

  info(message: string, context?: Record<string, unknown>): void {
    this.log('INFO', message, context);
  }

  warn(message: string, context?: Record<string, unknown>): void {
    this.log('WARN', message, context);
  }

  error(message: string, context?: Record<string, unknown>): void {
    this.log('ERROR', message, context);
  }

  private log(level: LogLevel, message: string, context?: Record<string, unknown>): void {
    // Suppress messages below configured threshold
    if (LEVEL_ORDER[level] < LEVEL_ORDER[currentLevel]) {
      return;
    }

    const method = CONSOLE_METHODS[level];

    // SSR safety: no-op if console method unavailable
    if (typeof console === 'undefined' || typeof console[method] !== 'function') {
      return;
    }

    const timestamp = new Date().toISOString();
    let formatted = `[${timestamp}] [${level}] [${this.module}] ${message}`;

    // Append correlation ID if active
    if (correlationId) {
      formatted += ` [cid:${correlationId}]`;
    }

    // Append context as JSON if provided
    if (context !== undefined) {
      try {
        formatted += ' ' + JSON.stringify(context);
      } catch {
        formatted += ' [unserializable context]';
      }
    }

    console[method](formatted);
  }
}

// --- Factory function ---

/**
 * Create a Logger instance for the given module name.
 */
export function createLogger(module: string): Logger {
  return new Logger(module);
}

// --- Global runtime control ---

// Expose window.__setLogLevel for runtime changes (browser only)
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__setLogLevel = (level: string): void => {
    setLogLevel(level);
  };
}
