/**
 * The price of one model, set from where the model is: the providers page.
 *
 * Adding a provider asked for a price once, in the wizard; after that the only
 * way was the observability pricing tab, knowing the exact provider and model
 * names the gateway files it under. Here the names come from the model, and
 * the entry is saved under them (a new price replaces the old one, which stays
 * in the history).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { observability, type PricingEntry } from "~/api/observability";
import { PriceField, perMillion, perThousand } from "~/components/PriceField";

export interface ModelPriceTarget {
  /** What the price is filed under (priceProviderKey / priceModelKey). */
  provider: string;
  model: string;
  /** What to call them in the dialog. */
  providerLabel: string;
  /** The price the gateway uses now, if any. */
  current: PricingEntry | null;
}

interface ModelPriceDialogProps {
  target: ModelPriceTarget | null;
  onClose: () => void;
  onSaved: () => void;
}

export function ModelPriceDialog({ target, onClose, onSaved }: ModelPriceDialogProps) {
  const { t } = useTranslation();
  const [inputPrice, setInputPrice] = useState<number | null>(null);
  const [outputPrice, setOutputPrice] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!target) return;
    setInputPrice(perMillion(target.current?.input_price_per_1k));
    setOutputPrice(perMillion(target.current?.output_price_per_1k));
    setError(null);
  }, [target]);

  // Filed under another provider's name (the gateway's fallback): saving
  // makes one of this provider's own.
  const current = target?.current ?? null;
  const own = current !== null && current.provider === target?.provider && current.model === target?.model;

  async function save() {
    if (!target || inputPrice === null || outputPrice === null) return;
    setSaving(true);
    setError(null);
    try {
      await observability.createPricing({
        provider: target.provider,
        model: target.model,
        input_price_per_1k: perThousand(inputPrice),
        output_price_per_1k: perThousand(outputPrice),
        currency: "USD",
      });
      onSaved();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("observability.create_failed"));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!current || !own) return;
    setSaving(true);
    setError(null);
    try {
      await observability.deactivatePricing(current.id);
      onSaved();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("observability.delete_failed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={target !== null} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{t("llm_providers.price_title", { model: target?.model ?? "" })}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            {t("llm_providers.price_help", { model: target?.model ?? "", provider: target?.providerLabel ?? "" })}
          </Typography>
          {current && !own && (
            <Alert severity="info">
              {t("llm_providers.price_borrowed", { provider: current.provider })}
            </Alert>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          <PriceField
            label={t("observability.input_price_per_1m")}
            value={inputPrice}
            onChange={setInputPrice}
            size="small"
            fullWidth
            required
            autoFocus
          />
          <PriceField
            label={t("observability.output_price_per_1m")}
            value={outputPrice}
            onChange={setOutputPrice}
            size="small"
            fullWidth
            required
            helperText={t("observability.price_per_1m_help")}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        {own && (
          <Button color="error" onClick={() => void remove()} disabled={saving} sx={{ mr: "auto" }}>
            {t("llm_providers.price_remove")}
          </Button>
        )}
        <Button onClick={onClose} disabled={saving}>
          {t("common.cancel")}
        </Button>
        <Button
          variant="contained"
          onClick={() => void save()}
          disabled={saving || inputPrice === null || outputPrice === null}
        >
          {saving ? t("observability.saving") : t("common.save")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
