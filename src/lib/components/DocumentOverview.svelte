<script lang="ts">
  import type { Snippet } from "svelte";
  import type { CustomerSnapshot } from "$lib/db/schema";
  import { Card, CardContent } from "$lib/ui";

  let { customer, children }: { customer: CustomerSnapshot; children: Snippet } = $props();
</script>

<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
  <Card>
    <CardContent>
      <h2 class="text-xs font-semibold uppercase text-muted-foreground tracking-wider mb-3">
        Kunde (Snapshot)
      </h2>
      <div class="text-sm space-y-0.5">
        <div class="font-medium">{customer.name}</div>
        {#if customer.contactPerson}<div>{customer.contactPerson}</div>{/if}
        <div class="text-muted-foreground">{customer.street}</div>
        <div class="text-muted-foreground">{customer.postalCode} {customer.city}</div>
        <div class="text-muted-foreground">{customer.country}</div>
        {#if customer.vatId}
          <div class="text-xs mt-2 text-muted-foreground">USt-IdNr.: {customer.vatId}</div>
        {/if}
      </div>
    </CardContent>
  </Card>

  <Card>
    <CardContent>
      <h2 class="text-xs font-semibold uppercase text-muted-foreground tracking-wider mb-3">
        Eckdaten
      </h2>
      <dl class="text-sm grid grid-cols-2 gap-y-1.5">
        {@render children()}
      </dl>
    </CardContent>
  </Card>
</div>
