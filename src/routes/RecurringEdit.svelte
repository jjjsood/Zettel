<script lang="ts">
  import { link, push } from "svelte-spa-router";
  import { listCustomers, loadSettings } from "$lib/db/queries";
  import {
    createRecurring,
    getRecurring,
    updateRecurring,
    type RecurringFormInput,
  } from "$lib/db/recurring";
  import { computeTotals, getInvoice } from "$lib/db/invoices";
  import type { Customer, Settings, RecurringInterval } from "$lib/db/schema";
  import { fromIsoDate, nowUnix, toIsoDate } from "$lib/utils/date";
  import {
    Button,
    Input,
    Textarea,
    Label,
    Card,
    CardContent,
    DatePicker,
    Select,
    Checkbox,
    toast,
  } from "$lib/ui";
  import { ArrowLeft } from "@lucide/svelte";
  import LineItemsEditor, { emptyItem, toLineItem, type LineItem } from "$lib/components/LineItemsEditor.svelte";

  type Props = {
    mode: "new" | "edit";
    params?: { id?: string };
    querystring?: string;
  };
  let { mode, params, querystring }: Props = $props();

  const fromInvoiceId = $derived.by(() => {
    if (mode !== "new" || !querystring) return null;
    const v = new URLSearchParams(querystring).get("fromInvoice");
    if (!v) return null;
    const n = Number.parseInt(v, 10);
    return Number.isFinite(n) ? n : null;
  });

  let customers = $state<Customer[]>([]);
  let settings = $state<Settings | null>(null);
  let id = $state<number | null>(null);
  let loaded = $state(false);
  const loading = $derived(mode === "edit" && !loaded);

  let saving = $state(false);
  let error = $state<string | null>(null);

  let name = $state("");
  let customerIdStr = $state<string>("");
  let interval = $state<RecurringInterval>("monthly");
  let startDateIso = $state(toIsoDate(nowUnix()));
  let paymentTermsDays = $state(14);
  let isReverseCharge = $state(false);
  let active = $state(true);
  let notes = $state("");
  let paymentTerms = $state("");
  let items = $state<LineItem[]>([emptyItem()]);

  const selectedCustomer = $derived(
    customerIdStr ? customers.find((c) => String(c.id) === customerIdStr) ?? null : null,
  );

  const canReverseCharge = $derived(
    !!settings &&
      !settings.isKleinunternehmer &&
      !!settings.vatId &&
      !!selectedCustomer?.vatId,
  );

  $effect(() => {
    if (!canReverseCharge && isReverseCharge) isReverseCharge = false;
  });

  $effect(() => {
    if (isReverseCharge) {
      let changed = false;
      for (const it of items) {
        if (it.vatRate !== 0) {
          it.vatRate = 0;
          changed = true;
        }
      }
      if (changed) items = [...items];
    }
  });

  const vatExempt = $derived(!!settings?.isKleinunternehmer || isReverseCharge);

  $effect(() => {
    Promise.all([loadSettings(), listCustomers()])
      .then(async ([s, cs]) => {
        settings = s;
        customers = cs;
        if (mode === "new") {
          paymentTermsDays = s.defaultPaymentTermsDays;
          for (const it of items) it.vatRate = s.isKleinunternehmer ? 0 : 19;
          items = [...items];
          if (fromInvoiceId !== null) {
            const res = await getInvoice(fromInvoiceId);
            if (res) {
              customerIdStr = String(res.invoice.customerId);
              isReverseCharge = res.invoice.isReverseCharge;
              paymentTerms = res.invoice.paymentTerms ?? "";
              const days = Math.max(
                0,
                Math.round((res.invoice.dueDate - res.invoice.issueDate) / 86400),
              );
              paymentTermsDays = days || s.defaultPaymentTermsDays;
              if (res.items.length > 0) {
                items = res.items.map(toLineItem);
              }
              startDateIso = "";
            }
          }
        }
      })
      .catch((e) => (error = String(e)));
  });

  $effect(() => {
    if (mode === "edit" && params?.id) {
      const numId = Number.parseInt(params.id, 10);
      if (Number.isNaN(numId)) return;
      id = numId;
      getRecurring(numId)
        .then((res) => {
          if (!res) {
            error = "Vorlage nicht gefunden.";
            return;
          }
          name = res.recurring.name;
          customerIdStr = String(res.recurring.customerId);
          interval = res.recurring.interval;
          startDateIso = toIsoDate(res.recurring.startDate);
          paymentTermsDays = res.recurring.paymentTermsDays;
          isReverseCharge = res.recurring.isReverseCharge;
          active = res.recurring.active;
          notes = res.recurring.notes ?? "";
          paymentTerms = res.recurring.paymentTerms ?? "";
          items = res.items.map(toLineItem);
        })
        .catch((e) => (error = String(e)))
        .finally(() => (loaded = true));
    }
  });

  const totals = $derived.by(() => {
    if (!settings) return { subtotal: 0, vatAmount: 0, total: 0 };
    return computeTotals(items, {
      isKleinunternehmer: settings.isKleinunternehmer,
      isReverseCharge,
    });
  });

  const customerItems = $derived(
    customers.map((c) => ({ value: String(c.id), label: `${c.customerNumber} · ${c.name}` })),
  );

  const intervalItems = [
    { value: "monthly", label: "Monatlich" },
    { value: "quarterly", label: "Quartalsweise" },
    { value: "yearly", label: "Jährlich" },
  ];

  async function onSubmit(e: SubmitEvent) {
    e.preventDefault();
    const customerId = customerIdStr ? Number.parseInt(customerIdStr, 10) : null;
    if (!name.trim()) {
      error = "Bitte einen Namen vergeben.";
      return;
    }
    if (customerId === null || Number.isNaN(customerId)) {
      error = "Bitte einen Kunden auswählen.";
      return;
    }
    if (items.length === 0) {
      error = "Mindestens eine Position erforderlich.";
      return;
    }
    saving = true;
    error = null;
    try {
      const input: RecurringFormInput = {
        name: name.trim(),
        customerId,
        interval,
        startDate: fromIsoDate(startDateIso),
        paymentTermsDays,
        isReverseCharge,
        notes: notes.trim() || null,
        paymentTerms: paymentTerms.trim() || null,
        active,
        items: items.map((it) => ({
          description: it.description,
          quantity: it.quantity,
          unit: it.unit,
          unitPrice: it.unitPrice,
          vatRate: it.vatRate,
          longDescription:
            it.showDetail && it.longDescription.trim() ? it.longDescription.trim() : null,
          linePeriodStart: it.periodStartIso ? fromIsoDate(it.periodStartIso) : null,
          linePeriodEnd: it.periodEndIso ? fromIsoDate(it.periodEndIso) : null,
        })),
      };
      let savedId: number;
      if (mode === "new") {
        savedId = await createRecurring(input);
        toast.success("Vorlage erstellt");
      } else if (id !== null) {
        await updateRecurring(id, input);
        toast.success("Änderungen gespeichert");
        savedId = id;
      } else {
        throw new Error("Keine ID");
      }
      push(`/recurring`);
      void savedId;
    } catch (err) {
      error = String(err);
      toast.error("Speichern fehlgeschlagen", String(err));
    } finally {
      saving = false;
    }
  }
</script>

<header class="mb-6">
  <a
    href="/recurring"
    use:link
    class="group inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
  >
    <ArrowLeft class="size-4 transition-transform group-hover:-translate-x-0.5" /> Wiederkehrende Rechnungen
  </a>
  <h1 class="text-3xl font-semibold tracking-tight mt-2">
    {mode === "new" ? "Neue Vorlage" : "Vorlage bearbeiten"}
  </h1>
</header>

{#if loading}
  <p class="text-sm text-muted-foreground">Lade…</p>
{:else}
  <form onsubmit={onSubmit} class="space-y-6">
    <Card>
      <CardContent>
        <section class="grid grid-cols-2 gap-4">
          <div class="col-span-2 flex flex-col gap-1.5">
            <Label>Bezeichnung <span class="text-destructive">*</span></Label>
            <Input bind:value={name} placeholder="z. B. Monatlicher Retainer Acme" required />
          </div>
          <div class="col-span-2 flex flex-col gap-1.5">
            <Label>Kunde <span class="text-destructive">*</span></Label>
            <Select bind:value={customerIdStr} items={customerItems} placeholder="— bitte wählen —" />
          </div>
          <div class="flex flex-col gap-1.5">
            <Label>Intervall</Label>
            <Select bind:value={interval as unknown as string} items={intervalItems} />
          </div>
          <div class="flex flex-col gap-1.5">
            <Label>Erste Fälligkeit</Label>
            <DatePicker bind:value={startDateIso} required />
          </div>
          <div class="flex flex-col gap-1.5">
            <Label>Zahlungsfrist (Tage)</Label>
            <Input type="number" min="0" bind:value={paymentTermsDays} />
          </div>
          <label class="flex items-center gap-2.5 text-sm cursor-pointer select-none">
            <Checkbox bind:checked={active} />
            Aktiv (erscheint im Dashboard)
          </label>
          {#if settings && !settings.isKleinunternehmer}
            <div class="col-span-2 flex items-start gap-3 rounded-md border border-border bg-muted/30 p-3">
              <Checkbox
                id="rc-recurring"
                bind:checked={isReverseCharge}
                disabled={!canReverseCharge}
                class="mt-0.5"
              />
              <div class="flex-1 text-sm">
                <label for="rc-recurring" class="font-medium cursor-pointer">
                  Reverse-Charge (intra-EU B2B)
                </label>
                <p class="text-xs text-muted-foreground mt-0.5">
                  Wird bei jeder erzeugten Rechnung übernommen. Setzt 0&nbsp;% USt und den Hinweistext.
                </p>
              </div>
            </div>
          {/if}
        </section>
      </CardContent>
    </Card>

    <LineItemsEditor
      bind:items
      {totals}
      {vatExempt}
      context="recurring"
      totalLabel="Voraussichtlicher Gesamtbetrag pro Erzeugung"
    />

    <Card>
      <CardContent class="space-y-4">
        <div class="flex flex-col gap-1.5">
          <Label>Zahlungsbedingungen</Label>
          <Textarea rows={2} bind:value={paymentTerms} />
        </div>
        <div class="flex flex-col gap-1.5">
          <Label>Notizen / Fußtext</Label>
          <Textarea rows={3} bind:value={notes} />
        </div>
      </CardContent>
    </Card>

    <div class="flex items-center gap-3">
      <Button type="submit" disabled={saving}>
        {saving ? "Speichere…" : mode === "new" ? "Anlegen" : "Speichern"}
      </Button>
      <a href="/recurring" use:link>
        <Button type="button" variant="ghost">Abbrechen</Button>
      </a>
      {#if error}
        <span class="text-sm text-destructive">{error}</span>
      {/if}
    </div>
  </form>

{/if}
