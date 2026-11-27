/**
 * SummaryModelsField — Settings > Chat > Summaries of long chats: one switch per chat model.
 *
 * The setting (``chat.summary_models``) is a list of model names; on = the name is in the list.
 * A name in the list that is no longer a model stays in it, shown as a switch marked "not found",
 * so saving does not drop it silently.
 */
import { useEffect, useState } from "react";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControlLabel from "@mui/material/FormControlLabel";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

import { chatApi } from "~/api/chatClient";
import { isChatModel } from "~/api/chatTypes";

export const SUMMARY_MODELS_KEY = "chat.summary_models";

/** The setting's value as a list of names; anything else (a value not yet read) is none. */
export function summaryModelNames(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

interface Props {
  label: string;
  /** The registry's own description. */
  description: string;
  value: unknown;
  onChange: (names: string[]) => void;
}

export default function SummaryModelsField({ label, description, value, onChange }: Props) {
  const { t } = useTranslation();
  const [models, setModels] = useState<string[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    chatApi
      .listModels()
      .then((all) => alive && setModels(all.filter(isChatModel).map((m) => m.alias)))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const names = summaryModelNames(value);
  const known = models ?? [];
  const missing = models ? names.filter((name) => !known.includes(name)) : [];
  // Without the list, the saved names still show (and stay).
  const rows = models ? [...known, ...missing] : names;

  const toggle = (name: string, on: boolean) =>
    onChange(on ? (names.includes(name) ? names : [...names, name]) : names.filter((n) => n !== name));

  return (
    <Box sx={{ flexGrow: 1 }} data-testid="summary-models">
      <Typography variant="body2">{label}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        {description}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
        {t("settings.summary_models_hint")}
      </Typography>
      {models === null && !failed && <CircularProgress size={18} />}
      {failed && (
        <Typography variant="caption" color="warning.main" sx={{ display: "block" }}>
          {t("settings.summary_models_failed")}
        </Typography>
      )}
      {models !== null && rows.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          {t("settings.summary_models_none")}
        </Typography>
      )}
      <Box sx={{ display: "flex", flexDirection: "column" }}>
        {rows.map((name) => (
          <FormControlLabel
            key={name}
            control={
              <Switch
                size="small"
                checked={names.includes(name)}
                onChange={(event) => toggle(name, event.target.checked)}
              />
            }
            label={
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <Typography variant="body2" component="span">
                  {name}
                </Typography>
                {models && missing.includes(name) && (
                  <Chip size="small" variant="outlined" color="warning" label={t("settings.summary_models_not_found")} />
                )}
              </Box>
            }
          />
        ))}
      </Box>
    </Box>
  );
}
