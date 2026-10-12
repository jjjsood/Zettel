import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { addDaysUnix, formatDate, fromIsoDate, nowUnix, toIsoDate } from "./date";

describe("toIsoDate / fromIsoDate", () => {
  it("roundtrips at local midnight", () => {
    const iso = "2026-05-22";
    expect(fromIsoDate(iso)).toBe(new Date(2026, 4, 22, 0, 0, 0).getTime() / 1000);
    expect(toIsoDate(fromIsoDate(iso))).toBe(iso);
  });
  it("zero-pads month and day", () => {
    const iso = toIsoDate(fromIsoDate("2026-01-05"));
    expect(iso).toBe("2026-01-05");
  });
  it("handles end-of-year boundary", () => {
    expect(toIsoDate(fromIsoDate("2025-12-31"))).toBe("2025-12-31");
  });
  it("handles leap day", () => {
    expect(toIsoDate(fromIsoDate("2024-02-29"))).toBe("2024-02-29");
  });
});

describe("fromIsoDate", () => {
  it("falls back to month/day 1 on partial input", () => {
    expect(fromIsoDate("2026")).toBe(fromIsoDate("2026-01-01"));
  });
});

describe("formatDate", () => {
  it("formats as D.M.YYYY (de-DE, day and month not zero-padded)", () => {
    expect(formatDate(fromIsoDate("2026-05-22"))).toBe("22.5.2026");
    expect(formatDate(fromIsoDate("2026-01-05"))).toBe("5.1.2026");
    expect(formatDate(fromIsoDate("2026-12-22"))).toBe("22.12.2026");
  });
});

describe("addDaysUnix", () => {
  it("adds positive days", () => {
    const base = fromIsoDate("2026-05-20");
    expect(toIsoDate(addDaysUnix(base, 7))).toBe("2026-05-27");
  });
  it("subtracts when negative", () => {
    const base = fromIsoDate("2026-05-01");
    expect(toIsoDate(addDaysUnix(base, -1))).toBe("2026-04-30");
  });
});

describe("addDaysUnix across DST (Europe/Berlin)", () => {
  beforeAll(() => {
    vi.stubEnv("TZ", "Europe/Berlin");
  });
  afterAll(() => {
    vi.unstubAllEnvs();
  });

  it("spring forward: +2 days from local midnight stays at local midnight on the calendar date", () => {
    const plus2 = addDaysUnix(fromIsoDate("2026-03-28"), 2);
    expect(plus2).toBe(fromIsoDate("2026-03-30"));
    expect(new Date(plus2 * 1000).getHours()).toBe(0);
  });
  it("fall back: +2 days from local midnight stays on the calendar date", () => {
    expect(toIsoDate(addDaysUnix(fromIsoDate("2026-10-24"), 2))).toBe("2026-10-26");
  });
});

describe("nowUnix", () => {
  it("returns a sane current epoch (seconds since 1970)", () => {
    const n = nowUnix();
    // some safe lower bound (year 2026) and reasonable upper bound (year 2100)
    expect(n).toBeGreaterThan(1_750_000_000);
    expect(n).toBeLessThan(4_000_000_000);
  });
  it("returns integer seconds", () => {
    expect(Number.isInteger(nowUnix())).toBe(true);
  });
});
