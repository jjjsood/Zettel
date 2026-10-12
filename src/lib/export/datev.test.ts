import { describe, it, expect } from "vitest";
import {
  buildDatevCsv,
  defaultDatevFilename,
  SKR03,
  SKR04,
  UTF8_BOM,
  type DatevExportOpts,
} from "./datev";

const opts: DatevExportOpts = {
  beraternr: 1001,
  mandantennr: 1,
  wjBeginn: new Date(2026, 0, 1),
  dateFrom: new Date(2026, 0, 1),
  dateTo: new Date(2026, 11, 31),
  sachkontenlaenge: 4,
  bezeichnung: "Zettel Test Export",
  accounts: SKR03,
};

describe("buildDatevCsv (empty)", () => {
  const csv = buildDatevCsv({ invoices: [], expenses: [] }, opts, new Date(2026, 4, 17, 12, 0, 0));

  it("starts with UTF-8 BOM", () => {
    expect(csv.startsWith(UTF8_BOM)).toBe(true);
  });

  it("uses CRLF line endings", () => {
    expect(csv).toContain("\r\n");
    expect(csv).not.toMatch(/[^\r]\n/);
  });

  it("emits exactly the header and column lines when no bookings", () => {
    const lines = csv.replace(UTF8_BOM, "").split("\r\n").filter(Boolean);
    expect(lines).toHaveLength(2);
  });

  it("header line starts with EXTF marker", () => {
    const firstLine = csv.replace(UTF8_BOM, "").split("\r\n")[0];
    expect(firstLine.startsWith('"EXTF"')).toBe(true);
  });

  it("includes consultant and client numbers in header", () => {
    const fields = csv.replace(UTF8_BOM, "").split("\r\n")[0].split(";");
    expect(fields[10]).toBe("1001");
    expect(fields[11]).toBe("1");
  });
});

describe("SKR03 vs SKR04 revenue accounts", () => {
  it("map 19 % revenue to 8400 (SKR03) and 4400 (SKR04)", () => {
    expect(SKR03.revenue19).toBe(8400);
    expect(SKR04.revenue19).toBe(4400);
  });
});

describe("defaultDatevFilename", () => {
  it("embeds local date and HHMM", () => {
    expect(defaultDatevFilename(new Date(2026, 4, 17, 14, 32))).toBe("EXTF_Buchungsstapel_20260517_1432.csv");
    expect(defaultDatevFilename(new Date(2026, 0, 5, 9, 7))).toBe("EXTF_Buchungsstapel_20260105_0907.csv");
  });
});
