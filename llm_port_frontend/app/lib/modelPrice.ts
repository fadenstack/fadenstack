/**
 * Which catalogue price the gateway charges a model with -- the same rule it
 * applies (llm_port_api services/gateway/pricing.py): the entry for the
 * provider and the name clients call the model by; failing that, the model's
 * only entry, whoever it is filed under.
 */
import type { PricingEntry } from "~/api/observability";
import type { Provider, Runtime } from "~/api/llm";

/** What a price for this provider is filed under: its LiteLLM provider, or its name. */
export function priceProviderKey(provider: Provider): string {
  return (provider.litellm_provider || provider.name).trim().toLowerCase();
}

/**
 * The name clients call the model by, which the gateway prices by: the
 * runtime's name; a deployment's provider has no runtime and goes by its own.
 */
export function priceModelKey(provider: Provider, runtime: Runtime | null): string | null {
  const name = runtime?.name ?? (provider.managed_by ? provider.name : null);
  return name ? name.trim().toLowerCase() : null;
}

export function findPrice(
  entries: PricingEntry[],
  provider: string,
  model: string,
): PricingEntry | null {
  const exact = entries.find((e) => e.active && e.provider === provider && e.model === model);
  if (exact) return exact;
  const byModel = entries.filter((e) => e.active && e.model === model);
  return byModel.length === 1 ? byModel[0] : null;
}
