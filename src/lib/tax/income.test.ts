import { describe, it, expect } from "vitest";
import { estimateIncomeTax, getTarifYear } from "./income";

// Golden-Cases pro Tarifjahr. Jede Annahme ist gegen die § 32a-Formel
// mit den dokumentierten Konstanten von Hand nachgerechnet — wer eine
// Konstante ändert, muss die Erwartungen mit-updaten.
//
// Berechnung der Erwartungen:
//   Zone 2: floor((z2a × y + z2b) × y)      y = (zvE − GF) / 10000
//   Zone 3: floor((z3a × z + z3b) × z + z3c) z = (zvE − Z2end) / 10000
//   Zone 4: floor(0.42 × zvE − z4Offset)
//   Zone 5: floor(0.45 × zvE − z5Offset)
//   Splitting: ESt(zvE/2) × 2
//   Soli:      min(0.055 × ESt, 0.119 × (ESt − Freigrenze)), floor

describe("estimateIncomeTax — Tarif 2024", () => {
  const Y = 2024;

  it("ESt = 0 unter Grundfreibetrag (10.000 €)", () => {
    expect(estimateIncomeTax(1_000_000, "single", 0, Y).est).toBe(0);
  });

  it("ESt = 0 bei genau Grundfreibetrag 11.604 €, erste Euro-ESt ab +8 €", () => {
    expect(estimateIncomeTax(1_160_400, "single", 0, Y).est).toBe(0);
    expect(estimateIncomeTax(1_161_100, "single", 0, Y).est).toBe(0);
    expect(estimateIncomeTax(1_161_200, "single", 0, Y).est).toBe(100);
  });

  it("Zone 2 — 15.000 € → 581 € ESt", () => {
    // y = 0.3396; (922.98*0.3396 + 1400) * 0.3396 = 581.8855... → 581
    expect(estimateIncomeTax(1_500_000, "single", 0, Y).est).toBe(58_100);
  });

  it("Zone 3 — 50.000 € → 10.906 € ESt", () => {
    // z = 3.2995; (181.19*3.2995 + 2397)*3.2995 + 1025.38 = 10906.85... → 10906
    expect(estimateIncomeTax(5_000_000, "single", 0, Y).est).toBe(1_090_600);
  });

  it("Zone 4 — 100.000 € → 31.397 € ESt", () => {
    // 0.42*100000 - 10602.13 = 31397.87 → 31397
    expect(estimateIncomeTax(10_000_000, "single", 0, Y).est).toBe(3_139_700);
  });

  it("Zone 5 — 300.000 € → 116.063 € ESt", () => {
    // 0.45*300000 - 18936.88 = 116063.12 → 116063
    expect(estimateIncomeTax(30_000_000, "single", 0, Y).est).toBe(11_606_300);
  });

  it("Splittingtarif 100k = 2 × ESt(50k single)", () => {
    expect(estimateIncomeTax(10_000_000, "married", 0, Y).est).toBe(2_181_200);
  });

  it("Splitting-Freibetrag doppelt = 23.208 € → 0 €", () => {
    expect(estimateIncomeTax(2_320_800, "married", 0, Y).est).toBe(0);
  });

  it("Soli = 0 unter Freigrenze (ESt 10.906 € < 18.130 €)", () => {
    expect(estimateIncomeTax(5_000_000, "single", 0, Y).soli).toBe(0);
  });

  it("Soli in Milderungszone (100k single, ESt 31.397)", () => {
    // milderung = 0.119 * (31397 - 18130) = 1578.77 → 1578
    expect(estimateIncomeTax(10_000_000, "single", 0, Y).soli).toBe(157_800);
  });

  it("Soli voller Satz (300k single)", () => {
    // 0.055 * 116063 = 6383.46 → 6383, kleiner als Milderungs-Variante
    expect(estimateIncomeTax(30_000_000, "single", 0, Y).soli).toBe(638_300);
  });

  it("KiSt 9 % auf ESt", () => {
    // floor(10906 * 0.09) = 981
    const r = estimateIncomeTax(5_000_000, "single", 0.09, Y);
    expect(r.kist).toBe(98_100);
  });

  it("Jahr außerhalb der Range → Tarif 2026 wird gerechnet und gemeldet", () => {
    const r = estimateIncomeTax(5_000_000, "single", 0, 2027);
    expect(r.tarifYear).toBe(2026);
    expect(r.est).toBe(1_054_800);
  });
});

describe("estimateIncomeTax — Tarif 2025", () => {
  const Y = 2025;

  it("ESt = 0 unter Grundfreibetrag (11.000 €)", () => {
    expect(estimateIncomeTax(1_100_000, "single", 0, Y).est).toBe(0);
  });

  it("ESt = 0 bei genau Grundfreibetrag 12.096 €, erste Euro-ESt ab +8 €", () => {
    expect(estimateIncomeTax(1_209_600, "single", 0, Y).est).toBe(0);
    expect(estimateIncomeTax(1_210_300, "single", 0, Y).est).toBe(0);
    expect(estimateIncomeTax(1_210_400, "single", 0, Y).est).toBe(100);
  });

  it("Zone 2 — 15.000 € → ESt nach Formel", () => {
    // y = (15000-12096)/10000 = 0.2904
    // (932.30*0.2904 + 1400)*0.2904 = (270.74 + 1400)*0.2904 = 1670.74*0.2904
    // = 485.18 → 485
    expect(estimateIncomeTax(1_500_000, "single", 0, Y).est).toBe(48_500);
  });

  it("Zone 3 — 50.000 € → ESt nach Formel", () => {
    // z = (50000-17443)/10000 = 3.2557
    // (176.64*3.2557 + 2397)*3.2557 + 1015.13
    // = (575.07 + 2397)*3.2557 + 1015.13 = 2972.07*3.2557 + 1015.13
    // = 9676.40 + 1015.13 = 10691.54 → 10691
    expect(estimateIncomeTax(5_000_000, "single", 0, Y).est).toBe(1_069_100);
  });

  it("Zone 4 — 100.000 € → 31.088 € ESt", () => {
    // 0.42*100000 - 10911.92 = 31088.08 → 31088
    expect(estimateIncomeTax(10_000_000, "single", 0, Y).est).toBe(3_108_800);
  });

  it("Zone 5 — 300.000 € → 115.753 € ESt", () => {
    // 0.45*300000 - 19246.67 = 115753.33 → 115753
    expect(estimateIncomeTax(30_000_000, "single", 0, Y).est).toBe(11_575_300);
  });

  it("Splittingtarif für 100k = 2 × ESt(50k single)", () => {
    // 2 × 10691 = 21382
    expect(estimateIncomeTax(10_000_000, "married", 0, Y).est).toBe(2_138_200);
  });

  it("Soli in Milderungszone (100k single)", () => {
    // milderung = 0.119 * (31088 - 19950) = 0.119*11138 = 1325.42 → 1325
    expect(estimateIncomeTax(10_000_000, "single", 0, Y).soli).toBe(132_500);
  });
});

describe("estimateIncomeTax — Tarif 2026", () => {
  const Y = 2026;

  it("ESt = 0 bis zum Grundfreibetrag 12.348 €, erste Euro-ESt ab +8 €", () => {
    expect(estimateIncomeTax(1_234_700, "single", 0, Y).est).toBe(0);
    expect(estimateIncomeTax(1_234_800, "single", 0, Y).est).toBe(0);
    expect(estimateIncomeTax(1_235_500, "single", 0, Y).est).toBe(0);
    expect(estimateIncomeTax(1_235_600, "single", 0, Y).est).toBe(100);
  });

  it("Zone 2 — 15.000 € → ESt nach Formel", () => {
    // y = (15000-12348)/10000 = 0.2652
    // (914.51*0.2652 + 1400)*0.2652 = (242.53 + 1400)*0.2652 = 1642.53*0.2652
    // = 435.6 → 435
    expect(estimateIncomeTax(1_500_000, "single", 0, Y).est).toBe(43_500);
  });

  it("Zone 3 — 50.000 € → ESt nach Formel", () => {
    // z = (50000-17799)/10000 = 3.2201
    // (173.10*3.2201 + 2397)*3.2201 + 1034.87
    // = (557.40 + 2397)*3.2201 + 1034.87 = 2954.40*3.2201 + 1034.87
    // = 9513.7 + 1034.87 = 10548.6 → 10548
    expect(estimateIncomeTax(5_000_000, "single", 0, Y).est).toBe(1_054_800);
  });

  it("Zone 4 — 100.000 € → 30.864 € ESt", () => {
    // 0.42*100000 - 11135.63 = 30864.37 → 30864
    expect(estimateIncomeTax(10_000_000, "single", 0, Y).est).toBe(3_086_400);
  });

  it("Zone 5 — 300.000 € → 115.529 € ESt", () => {
    // 0.45*300000 - 19470.38 = 115529.62 → 115529
    expect(estimateIncomeTax(30_000_000, "single", 0, Y).est).toBe(11_552_900);
  });

  it("Splittingtarif für 100k", () => {
    // 2 × est(50.000) = 2 × 10548 = 21096
    expect(estimateIncomeTax(10_000_000, "married", 0, Y).est).toBe(2_109_600);
  });

  it("KiSt 8 % BY", () => {
    // floor(10548 * 0.08) = 843
    const r = estimateIncomeTax(5_000_000, "single", 0.08, Y);
    expect(r.kist).toBe(84_300);
  });
});

describe("estimateIncomeTax — Cross-Year-Eigenschaften", () => {
  it("Steuerlast sinkt 2024 → 2025 → 2026 für gleiches Einkommen", () => {
    // Grundfreibetrag wird angehoben → ESt sinkt bei gleichem zvE.
    const e24 = estimateIncomeTax(5_000_000, "single", 0, 2024).est;
    const e25 = estimateIncomeTax(5_000_000, "single", 0, 2025).est;
    const e26 = estimateIncomeTax(5_000_000, "single", 0, 2026).est;
    expect(e25).toBeLessThan(e24);
    expect(e26).toBeLessThan(e25);
  });

  it("Splittingtarif spart bei jedem Jahr Steuern (married < single)", () => {
    for (const y of [2024, 2025, 2026] as const) {
      const single = estimateIncomeTax(10_000_000, "single", 0, y).est;
      const married = estimateIncomeTax(10_000_000, "married", 0, y).est;
      expect(married).toBeLessThan(single);
    }
  });

  it("total, soli und kist für 100k single bei 9 % KiSt (je Jahr)", () => {
    expect(estimateIncomeTax(10_000_000, "single", 0.09, 2024)).toEqual({
      est: 3_139_700, soli: 157_800, kist: 282_500, total: 3_580_000, tarifYear: 2024,
    });
    expect(estimateIncomeTax(10_000_000, "single", 0.09, 2025)).toEqual({
      est: 3_108_800, soli: 132_500, kist: 279_700, total: 3_521_000, tarifYear: 2025,
    });
    expect(estimateIncomeTax(10_000_000, "single", 0.09, 2026)).toEqual({
      est: 3_086_400, soli: 125_100, kist: 277_700, total: 3_489_200, tarifYear: 2026,
    });
  });
});

describe("estimateIncomeTax — Soli-Freigrenze", () => {
  // [Jahr, Status, zvE mit ESt == Freigrenze, zvE mit ESt ≈ Freigrenze + 9 € (Splitting: +10 €)]
  // Freigrenze: 18.130 / 19.950 / 20.350 € (verdoppelt bei Splitting)
  const cases = [
    [2024, "single", 68_410, 68_432],
    [2025, "single", 73_481, 73_503],
    [2026, "single", 74_966, 74_988],
    [2024, "married", 136_820, 136_844],
    [2025, "married", 146_962, 146_986],
    [2026, "married", 149_932, 149_956],
  ] as const;
  const freigrenze = { 2024: 18_130, 2025: 19_950, 2026: 20_350 } as const;

  it.each(cases)("%i %s: Soli 0 bei ESt = Freigrenze, knapp darüber greift die Milderungszone", (y, status, atLimit, above) => {
    const f = freigrenze[y] * (status === "married" ? 2 : 1);
    const at = estimateIncomeTax(atLimit * 100, status, 0, y);
    expect(at.est).toBe(f * 100);
    expect(at.soli).toBe(0);
    const over = estimateIncomeTax(above * 100, status, 0, y);
    expect(over.est).toBe((f + (status === "married" ? 10 : 9)) * 100);
    expect(over.soli).toBe(100);
  });
});

describe("estimateIncomeTax — Stetigkeit an den Zonengrenzen", () => {
  const borders = {
    2024: [17_005, 66_760, 277_825],
    2025: [17_443, 68_480, 277_825],
    2026: [17_799, 69_878, 277_825],
  } as const;
  const jumps = (y: 2024 | 2025 | 2026) =>
    borders[y].map(
      (b) =>
        estimateIncomeTax((b + 1) * 100, "single", 0, y).est / 100 -
        estimateIncomeTax(b * 100, "single", 0, y).est / 100,
    );

  it.each([2024, 2025, 2026] as const)("%i: ESt springt an keiner Zonengrenze um mehr als 2 €", (y) => {
    for (const d of jumps(y)) expect(Math.abs(d)).toBeLessThanOrEqual(2);
  });
});

describe("getTarifYear — Year fallback", () => {
  it("hinterlegte Jahre direkt zurück", () => {
    expect(getTarifYear(2024)).toBe(2024);
    expect(getTarifYear(2025)).toBe(2025);
    expect(getTarifYear(2026)).toBe(2026);
  });

  it("Zukunfts-Jahre → neuestes hinterlegtes Jahr", () => {
    expect(getTarifYear(2027)).toBe(2026);
    expect(getTarifYear(2030)).toBe(2026);
  });
});
