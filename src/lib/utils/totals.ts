/**
 * Pure line-total / VAT math, shared across invoices, offers and expenses.
 *
 * Cent-integer all the way. Per-position rounding (Math.round) matches the
 * Finanzamt convention: each line is rounded individually before the VAT sum,
 * so the totals shown to the customer reconcile down to single cents.
 */

export type TotalsItem = {
  quantity: number;
  unitPrice: number; // cents
  vatRate: number; // percent (integer)
};

export type Totals = {
  subtotal: number;
  vatAmount: number;
  total: number;
};

export type TotalsOpts = {
  isKleinunternehmer: boolean;
  isReverseCharge: boolean;
};

/** Half away from zero, so a storno rounds to exactly the negative of its original (Math.round(-9.5) is -9). */
export function roundHalfAway(v: number): number {
  return v < 0 ? -Math.round(-v) : Math.round(v);
}

export function computeLineTotal(item: TotalsItem): number {
  return Math.round(item.quantity * item.unitPrice);
}

export function computeTotals(items: TotalsItem[], opts: TotalsOpts): Totals {
  const vatExempt = opts.isKleinunternehmer || opts.isReverseCharge;
  let subtotal = 0;
  let vatAmount = 0;
  for (const item of items) {
    const line = computeLineTotal(item);
    subtotal += line;
    if (!vatExempt) {
      vatAmount += roundHalfAway((line * item.vatRate) / 100);
    }
  }
  return { subtotal, vatAmount, total: subtotal + vatAmount };
}
