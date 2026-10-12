<script lang="ts">
  import type { InvoiceItem, OfferItem } from "$lib/db/schema";
  import { formatDate } from "$lib/utils/date";
  import { Card } from "$lib/ui";

  type Props = {
    items: (InvoiceItem | OfferItem)[];
    doc: { isKleinunternehmer: boolean; subtotal: number; vatAmount: number; total: number };
    money: (cents: number) => string;
  };
  let { items, doc, money }: Props = $props();
</script>

<Card class="overflow-hidden mb-6">
  <table class="w-full text-sm">
    <thead class="bg-muted/40 text-left">
      <tr class="text-xs uppercase tracking-wider text-muted-foreground">
        <th class="px-4 py-3 font-medium w-12">#</th>
        <th class="px-4 py-3 font-medium">Beschreibung</th>
        <th class="px-4 py-3 font-medium text-right w-20">Menge</th>
        <th class="px-4 py-3 font-medium w-20">Einheit</th>
        <th class="px-4 py-3 font-medium text-right w-28">Preis</th>
        {#if !doc.isKleinunternehmer}
          <th class="px-4 py-3 font-medium text-right w-16">USt%</th>
        {/if}
        <th class="px-4 py-3 font-medium text-right w-28">Summe</th>
      </tr>
    </thead>
    <tbody>
      {#each items as it (it.id)}
        <tr class="border-t border-border">
          <td class="px-4 py-3 text-xs text-muted-foreground align-top">{it.position}</td>
          <td class="px-4 py-3 align-top">
            <div>{it.description}</div>
            {#if it.longDescription}
              <div class="mt-0.5 text-xs text-muted-foreground whitespace-pre-wrap">{it.longDescription}</div>
            {/if}
            {#if it.linePeriodStart && it.linePeriodEnd}
              <div class="mt-0.5 text-xs text-muted-foreground italic">
                {#if it.linePeriodStart === it.linePeriodEnd}
                  Leistungsdatum {formatDate(it.linePeriodStart)}
                {:else}
                  Leistungszeitraum {formatDate(it.linePeriodStart)} – {formatDate(it.linePeriodEnd)}
                {/if}
              </div>
            {/if}
          </td>
          <td class="px-4 py-3 text-right align-top">{it.quantity}</td>
          <td class="px-4 py-3 align-top">{it.unit}</td>
          <td class="px-4 py-3 text-right font-mono align-top">{money(it.unitPrice)}</td>
          {#if !doc.isKleinunternehmer}
            <td class="px-4 py-3 text-right align-top">{it.vatRate}</td>
          {/if}
          <td class="px-4 py-3 text-right font-mono align-top">{money(it.lineTotal)}</td>
        </tr>
      {/each}
    </tbody>
    <tfoot class="bg-muted/40">
      <tr class="border-t border-border">
        <td colspan={doc.isKleinunternehmer ? 5 : 6} class="px-4 py-2 text-right text-muted-foreground">
          Zwischensumme
        </td>
        <td class="px-4 py-2 text-right font-mono">{money(doc.subtotal)}</td>
      </tr>
      {#if !doc.isKleinunternehmer}
        <tr>
          <td colspan="6" class="px-4 py-2 text-right text-muted-foreground">USt</td>
          <td class="px-4 py-2 text-right font-mono">{money(doc.vatAmount)}</td>
        </tr>
      {/if}
      <tr class="border-t border-border">
        <td colspan={doc.isKleinunternehmer ? 5 : 6} class="px-4 py-3 text-right font-semibold">
          Gesamtbetrag
        </td>
        <td class="px-4 py-3 text-right font-mono font-semibold">{money(doc.total)}</td>
      </tr>
    </tfoot>
  </table>
</Card>
