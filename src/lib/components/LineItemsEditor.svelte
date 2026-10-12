<script module lang="ts">
  import type { InvoiceItemInput } from "$lib/db/invoices";
  import { toIsoDate } from "$lib/utils/date";

  export type LineItem = InvoiceItemInput & {
    uid: number;
    priceText: string;
    longDescription: string;
    periodStartIso: string;
    periodEndIso: string;
    showDetail: boolean;
  };

  // Stable per-row id so keyed {#each} + animate:flip survive reordering.
  let itemUid = 0;

  function priceText(cents: number): string {
    return (cents / 100).toFixed(2).replace(".", ",");
  }

  export function emptyItem(vatRate = 0): LineItem {
    return {
      uid: ++itemUid,
      description: "",
      quantity: 1,
      unit: "Stk",
      unitPrice: 0,
      vatRate,
      priceText: "",
      longDescription: "",
      periodStartIso: "",
      periodEndIso: "",
      showDetail: false,
    };
  }

  export function toLineItem(it: InvoiceItemInput): LineItem {
    return {
      uid: ++itemUid,
      description: it.description,
      quantity: it.quantity,
      unit: it.unit,
      unitPrice: it.unitPrice,
      vatRate: it.vatRate,
      priceText: priceText(it.unitPrice),
      longDescription: it.longDescription ?? "",
      periodStartIso: it.linePeriodStart ? toIsoDate(it.linePeriodStart) : "",
      periodEndIso: it.linePeriodEnd ? toIsoDate(it.linePeriodEnd) : "",
      showDetail: !!it.longDescription,
    };
  }
</script>

<script lang="ts">
  import { computeLineTotal } from "$lib/db/invoices";
  import type { CatalogItem } from "$lib/db/schema";
  import { eurStringToCents } from "$lib/utils/money";
  import { formatMoney } from "$lib/utils/currency";
  import { Button, Input, Textarea, Card, CatalogPicker, LinePeriodPopover, Select } from "$lib/ui";
  import { Plus, Trash2, FileText, X, Package, GripVertical, ChevronUp, ChevronDown } from "@lucide/svelte";
  import { flip } from "svelte/animate";

  type Props = {
    items: LineItem[];
    totals: { subtotal: number; vatAmount: number; total: number };
    vatExempt: boolean;
    context: "invoice" | "offer" | "recurring";
    /** Omitted → EUR, header shows "€". */
    currency?: string;
    language?: "de" | "en";
    reorderable?: boolean;
    readOnly?: boolean;
    detailPlaceholder?: string;
    /** Set → footer shows only the total row, with this label. */
    totalLabel?: string;
  };
  let {
    items = $bindable(),
    totals,
    vatExempt,
    context,
    currency,
    language = "de",
    reorderable = false,
    readOnly = false,
    detailPlaceholder = "Detail-Beschreibung",
    totalLabel,
  }: Props = $props();

  const money = (cents: number) => formatMoney(cents, currency ?? "EUR");

  const vatItems = [
    { value: "0", label: "0 %" },
    { value: "7", label: "7 %" },
    { value: "19", label: "19 %" },
  ];

  function addItem() {
    items = [...items, emptyItem(vatExempt ? 0 : 19)];
  }

  let catalogPickerOpen = $state(false);
  function addFromCatalog(it: CatalogItem) {
    const next = emptyItem(vatExempt ? 0 : it.defaultVatRate);
    next.description = it.name;
    next.quantity = 1;
    next.unit = it.unit;
    next.unitPrice = it.defaultUnitPrice;
    next.priceText = priceText(it.defaultUnitPrice);
    const desc = language === "en" && it.descriptionEn ? it.descriptionEn : it.descriptionDe;
    if (desc) {
      next.longDescription = desc;
      next.showDetail = true;
    }
    items = [...items, next];
  }

  function removeItem(idx: number) {
    items = items.filter((_, i) => i !== idx);
  }

  function moveItem(from: number, to: number) {
    if (readOnly || from === to || to < 0 || to >= items.length) return;
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    items = next;
  }

  let dragIndex = $state<number | null>(null);
  let dragOverIndex = $state<number | null>(null);
  function onDragStart(idx: number) {
    if (readOnly) return;
    dragIndex = idx;
  }
  function onDragOver(e: DragEvent, idx: number) {
    if (readOnly || !reorderable) return;
    e.preventDefault();
    dragOverIndex = idx;
  }
  function onDrop(idx: number) {
    if (dragIndex !== null) moveItem(dragIndex, idx);
    dragIndex = null;
    dragOverIndex = null;
  }
  function onDragEnd() {
    dragIndex = null;
    dragOverIndex = null;
  }

  function onPriceBlur(idx: number) {
    const it = items[idx];
    it.unitPrice = eurStringToCents(it.priceText);
    it.priceText = priceText(it.unitPrice);
    items = [...items];
  }
</script>

{#snippet removeButton(idx: number)}
  <button
    type="button"
    onclick={() => removeItem(idx)}
    disabled={items.length === 1 || readOnly}
    class="inline-flex items-center justify-center size-8 rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-30 disabled:pointer-events-none transition-colors"
    title="Position entfernen"
  >
    <Trash2 class="size-4" />
  </button>
{/snippet}

<section>
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-sm font-semibold uppercase text-muted-foreground tracking-wider">
      Positionen
    </h2>
    <div class="inline-flex items-center gap-2">
      <Button type="button" size="sm" variant="outline" onclick={() => (catalogPickerOpen = true)} disabled={readOnly}>
        <Package />
        Aus Katalog…
      </Button>
      <Button type="button" size="sm" variant="outline" onclick={addItem} disabled={readOnly}>
        <Plus />
        Position
      </Button>
    </div>
  </div>
  <Card class="overflow-hidden py-0">
    <table class="w-full text-sm">
      <thead class="bg-muted/40 text-left">
        <tr class="text-xs uppercase tracking-wider text-muted-foreground">
          <th class="px-3 py-2 {reorderable ? 'w-14' : 'w-10'} font-medium">#</th>
          <th class="px-3 py-2 font-medium">Beschreibung</th>
          <th class="px-3 py-2 w-20 font-medium">Menge</th>
          <th class="px-3 py-2 w-20 font-medium">Einheit</th>
          <th class="px-3 py-2 w-28 font-medium">Preis ({currency ?? "€"})</th>
          {#if !vatExempt}
            <th class="px-3 py-2 w-24 font-medium">USt</th>
          {/if}
          <th class="px-3 py-2 w-28 font-medium text-right">Summe</th>
          <th class="px-3 py-2 {reorderable ? 'w-24' : 'w-10'} font-medium"></th>
        </tr>
      </thead>
      <tbody>
        {#each items as it, idx (reorderable ? it.uid : idx)}
          <tr
            class="border-t {dragOverIndex === idx && dragIndex !== idx ? 'bg-accent/40' : ''}"
            class:opacity-50={dragIndex === idx}
            animate:flip={{ duration: 150 }}
            ondragover={(e) => onDragOver(e, idx)}
            ondrop={() => onDrop(idx)}
          >
            {#if reorderable}
              <td class="px-2 py-2 align-top">
                <div class="flex items-center gap-1">
                  <button
                    type="button"
                    draggable={!readOnly}
                    ondragstart={() => onDragStart(idx)}
                    ondragend={onDragEnd}
                    disabled={readOnly}
                    class="cursor-grab active:cursor-grabbing text-muted-foreground/40 hover:text-foreground disabled:opacity-30 disabled:cursor-default transition-colors"
                    title="Ziehen zum Sortieren"
                    aria-label="Position verschieben"
                  >
                    <GripVertical class="size-4" />
                  </button>
                  <span class="text-xs text-muted-foreground tabular-nums">{idx + 1}</span>
                </div>
              </td>
            {:else}
              <td class="px-3 py-2 text-xs text-muted-foreground align-top pt-3">{idx + 1}</td>
            {/if}
            <td class="px-2 py-1.5">
              <Input bind:value={it.description} required disabled={readOnly} />
              {#if it.showDetail}
                <div class="mt-1.5">
                  <Textarea
                    rows={2}
                    placeholder={detailPlaceholder}
                    bind:value={it.longDescription}
                    disabled={readOnly}
                  />
                </div>
              {/if}
              <div class="mt-1.5 flex flex-wrap gap-1.5">
                <LinePeriodPopover
                  bind:startIso={it.periodStartIso}
                  bind:endIso={it.periodEndIso}
                  disabled={readOnly}
                />
                {#if it.showDetail}
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 rounded-md border border-input bg-transparent px-2 h-7 text-xs text-muted-foreground hover:bg-accent hover:text-destructive transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                    onclick={() => {
                      it.showDetail = false;
                      it.longDescription = "";
                      items = [...items];
                    }}
                    disabled={readOnly}
                  >
                    <X class="size-3" /> Detail entfernen
                  </button>
                {:else}
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 rounded-md border border-input bg-transparent px-2 h-7 text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                    onclick={() => {
                      it.showDetail = true;
                      items = [...items];
                    }}
                    disabled={readOnly}
                  >
                    <FileText class="size-3" /> Detail-Beschreibung
                  </button>
                {/if}
              </div>
            </td>
            <td class="px-2 py-1.5 align-top pt-1.5">
              <Input type="number" step="0.01" min="0" bind:value={it.quantity} disabled={readOnly} />
            </td>
            <td class="px-2 py-1.5 align-top pt-1.5">
              <Input bind:value={it.unit} disabled={readOnly} />
            </td>
            <td class="px-2 py-1.5 align-top pt-1.5">
              <Input
                bind:value={it.priceText}
                onblur={() => onPriceBlur(idx)}
                placeholder="0,00"
                class="text-right"
                disabled={readOnly}
              />
            </td>
            {#if !vatExempt}
              <td class="px-2 py-1.5 align-top pt-1.5">
                <Select
                  bind:value={
                    () => String(it.vatRate),
                    (v) => {
                      it.vatRate = Number.parseInt(v, 10);
                      items = [...items];
                    }
                  }
                  items={vatItems}
                  disabled={readOnly}
                />
              </td>
            {/if}
            <td class="px-3 py-2 text-right font-mono text-xs align-top pt-3">
              {money(computeLineTotal(it))}
            </td>
            <td class="px-2 py-1.5 text-right align-top pt-1.5">
              {#if reorderable}
                <div class="inline-flex items-center gap-0.5">
                  <div class="flex flex-col">
                    <button
                      type="button"
                      onclick={() => moveItem(idx, idx - 1)}
                      disabled={idx === 0 || readOnly}
                      class="inline-flex items-center justify-center size-5 rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-20 disabled:pointer-events-none transition-colors"
                      title="Nach oben"
                      aria-label="Position nach oben"
                    >
                      <ChevronUp class="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onclick={() => moveItem(idx, idx + 1)}
                      disabled={idx === items.length - 1 || readOnly}
                      class="inline-flex items-center justify-center size-5 rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-20 disabled:pointer-events-none transition-colors"
                      title="Nach unten"
                      aria-label="Position nach unten"
                    >
                      <ChevronDown class="size-3.5" />
                    </button>
                  </div>
                  {@render removeButton(idx)}
                </div>
              {:else}
                {@render removeButton(idx)}
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
      <tfoot class="bg-muted/40 text-sm">
        {#if !totalLabel}
          <tr class="border-t">
            <td colspan={vatExempt ? 5 : 6} class="px-3 py-2 text-right text-muted-foreground">
              Zwischensumme
            </td>
            <td class="px-3 py-2 text-right font-mono">{money(totals.subtotal)}</td>
            <td></td>
          </tr>
          {#if !vatExempt}
            <tr>
              <td colspan="6" class="px-3 py-2 text-right text-muted-foreground">USt</td>
              <td class="px-3 py-2 text-right font-mono">{money(totals.vatAmount)}</td>
              <td></td>
            </tr>
          {/if}
        {/if}
        <tr class="border-t">
          <td colspan={vatExempt ? 5 : 6} class="px-3 py-2 text-right font-semibold">
            {totalLabel ?? "Gesamtbetrag"}
          </td>
          <td class="px-3 py-2 text-right font-mono font-semibold">{money(totals.total)}</td>
          <td></td>
        </tr>
      </tfoot>
    </table>
  </Card>
</section>

<CatalogPicker bind:open={catalogPickerOpen} onPick={addFromCatalog} {context} {language} />
