/**
 * Centralized Date & Time formatting utilities for IST (Asia/Kolkata, UTC+05:30).
 *
 * Requirements:
 * 1. Canonical display timezone: 'Asia/Kolkata' (en-IN).
 * 2. Parses ISO strings, Date objects, and epoch timestamps reliably.
 * 3. Preserves underlying instants; safely treats naive ISO timestamps from the
 *    backend as UTC if they lack an explicit offset/Z (Tortoise/Postgres convention).
 * 4. Provides split date/time formatting for tabular directory views.
 */

export const IST_TIMEZONE = 'Asia/Kolkata';

/**
 * Safely parses any date-like input into a valid Date object.
 * If given a naive ISO string like "2026-10-21T10:00:00" without timezone offset,
 * treats it as UTC ("2026-10-21T10:00:00Z") in line with server database storage.
 */
export const parseTimestamp = (value: string | number | Date | null | undefined): Date | null => {
  if (value === null || value === undefined || value === '') {
    return null;
  }

  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }

  if (typeof value === 'number') {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  let str = String(value).trim();
  if (!str) return null;

  // Normalize SQLite / space-separated timestamps: 'YYYY-MM-DD HH:mm:ss...' -> 'YYYY-MM-DDTHH:mm:ss...'
  str = str.replace(/^(\d{4}-\d{2}-\d{2})\s+/, '$1T');

  // If naive ISO format YYYY-MM-DDTHH:mm:ss(.sss)? without Z or +HH:mm/-HH:mm,
  // append 'Z' because all stored DB timestamps in Tortoise/Postgres are UTC.
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(str)) {
    str += 'Z';
  }

  const d = new Date(str);
  return Number.isNaN(d.getTime()) ? null : d;
};

/**
 * Formats a timestamp into full IST representation:
 * e.g., "21 Oct 2026, 03:30 pm"
 */
export const formatDateTimeIST = (
  value: string | number | Date | null | undefined,
  fallback = '—'
): string => {
  const d = parseTimestamp(value);
  if (!d) return fallback;

  try {
    return new Intl.DateTimeFormat('en-IN', {
      timeZone: IST_TIMEZONE,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    }).format(d);
  } catch {
    return fallback;
  }
};

/**
 * Formats a timestamp into date-only IST representation:
 * e.g., "21 Oct 2026"
 */
export const formatDateIST = (
  value: string | number | Date | null | undefined,
  fallback = 'Date unknown'
): string => {
  const d = parseTimestamp(value);
  if (!d) return fallback;

  try {
    return new Intl.DateTimeFormat('en-IN', {
      timeZone: IST_TIMEZONE,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    }).format(d);
  } catch {
    return fallback;
  }
};

/**
 * Formats a timestamp into time-only IST representation:
 * e.g., "03:30 pm"
 */
export const formatTimeIST = (
  value: string | number | Date | null | undefined,
  fallback = ''
): string => {
  const d = parseTimestamp(value);
  if (!d) return fallback;

  try {
    return new Intl.DateTimeFormat('en-IN', {
      timeZone: IST_TIMEZONE,
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    }).format(d);
  } catch {
    return fallback;
  }
};

/**
 * Formats a timestamp into split date and time parts for directory table rendering.
 * e.g., { date: "21 Oct 2026", time: "03:30 pm" }
 */
export const formatDateTimePartsIST = (
  value: string | number | Date | null | undefined
): { date: string; time: string } => {
  const d = parseTimestamp(value);
  if (!d) {
    return { date: 'Date unknown', time: '' };
  }

  try {
    const dateStr = formatDateIST(d);
    const timeStr = formatTimeIST(d);
    return { date: dateStr, time: timeStr };
  } catch {
    return { date: 'Date unknown', time: '' };
  }
};
