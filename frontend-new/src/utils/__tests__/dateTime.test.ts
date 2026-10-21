import { describe, it, expect } from 'vitest';
import {
  formatDateTimeIST,
  formatDateIST,
  formatTimeIST,
  formatDateTimePartsIST,
} from '../dateTime';

describe('dateTime utility (IST Asia/Kolkata)', () => {
  it('correctly parses UTC ISO string with Z and converts to IST (+05:30)', () => {
    // 10:00:00 UTC = 15:30:00 IST (03:30 pm)
    const utcIso = '2026-10-21T10:00:00Z';
    const formatted = formatDateTimeIST(utcIso);
    expect(formatted).toBe('21 Oct 2026, 03:30 pm');

    const parts = formatDateTimePartsIST(utcIso);
    expect(parts.date).toBe('21 Oct 2026');
    expect(parts.time).toBe('03:30 pm');
  });

  it('correctly parses ISO string with explicit timezone offset', () => {
    // 15:30:00 +05:30 = 03:30 pm IST
    const offsetIso = '2026-10-21T15:30:00+05:30';
    const parts = formatDateTimePartsIST(offsetIso);
    expect(parts.date).toBe('21 Oct 2026');
    expect(parts.time).toBe('03:30 pm');
  });

  it('correctly parses SQLite space-separated timestamps with microsecond precision and offset', () => {
    // 17:40:09 UTC = 23:10:09 IST (11:10 pm)
    const sqliteIso = '2026-10-21 17:40:09.738686+00:00';
    const parts = formatDateTimePartsIST(sqliteIso);
    expect(parts.date).toBe('21 Oct 2026');
    expect(parts.time).toBe('11:10 pm');
    expect(formatDateTimeIST(sqliteIso)).toBe('21 Oct 2026, 11:10 pm');
  });

  it('safely handles naive ISO strings by treating them as UTC database timestamps', () => {
    // 04:15:00 UTC = 09:45:00 IST (09:45 am)
    const naiveIso = '2026-05-10T04:15:00';
    const parts = formatDateTimePartsIST(naiveIso);
    expect(parts.date).toBe('10 May 2026');
    expect(parts.time).toBe('09:45 am');
  });

  it('safely handles space-separated naive timestamps', () => {
    // 04:15:00 UTC = 09:45:00 IST (09:45 am)
    const naiveSpace = '2026-05-10 04:15:00';
    const parts = formatDateTimePartsIST(naiveSpace);
    expect(parts.date).toBe('10 May 2026');
    expect(parts.time).toBe('09:45 am');
  });

  it('handles midnight boundary across days accurately', () => {
    // 2026-10-21 21:00:00 UTC + 5:30 = 2026-10-22 02:30:00 IST
    const lateUtc = '2026-10-21T21:00:00Z';
    const parts = formatDateTimePartsIST(lateUtc);
    expect(parts.date).toBe('22 Oct 2026');
    expect(parts.time).toBe('02:30 am');
  });

  it('handles null, undefined, empty strings, and invalid dates gracefully', () => {
    expect(formatDateTimeIST(null)).toBe('—');
    expect(formatDateTimeIST(undefined)).toBe('—');
    expect(formatDateTimeIST('')).toBe('—');
    expect(formatDateTimeIST('invalid-date-string')).toBe('—');

    expect(formatDateIST(null)).toBe('Date unknown');
    expect(formatTimeIST(null)).toBe('');

    const emptyParts = formatDateTimePartsIST(null);
    expect(emptyParts.date).toBe('Date unknown');
    expect(emptyParts.time).toBe('');

    const invalidParts = formatDateTimePartsIST('not-a-date');
    expect(invalidParts.date).toBe('Date unknown');
    expect(invalidParts.time).toBe('');
  });

  it('handles native Date objects and numeric epoch timestamps', () => {
    const d = new Date('2026-10-21T10:00:00Z');
    expect(formatDateIST(d)).toBe('21 Oct 2026');
    expect(formatTimeIST(d)).toBe('03:30 pm');

    const epochMs = d.getTime();
    expect(formatDateTimeIST(epochMs)).toBe('21 Oct 2026, 03:30 pm');
  });
});
