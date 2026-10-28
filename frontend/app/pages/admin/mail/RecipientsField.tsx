/**
 * Who gets the alerts for administrators, besides the administrators themselves: a list of addresses. One is
 * added with Enter, the button or by leaving the field; a pasted list (commas, semicolons, spaces) adds each.
 * The list holds at most {@link MAX_RECIPIENTS}, as the server takes no more: a paste past it adds none.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";
import CancelIcon from "@mui/icons-material/Cancel";

import { isAddress, MAX_RECIPIENTS } from "./model";

export function RecipientsField({
  value,
  onChange,
  error,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  /** What the server said is wrong with the list. */
  error?: string;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  // Why the typed text was not added: not an address, or the list would be too long.
  const [refused, setRefused] = useState<"not_an_address" | "too_many" | null>(null);

  function add() {
    const items = draft
      .split(/[\s,;]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!items.length) return;
    if (!items.every(isAddress)) {
      setRefused("not_an_address");
      return;
    }
    const next = [...value];
    for (const item of items) {
      if (!next.some((known) => known.toLowerCase() === item.toLowerCase())) next.push(item);
    }
    if (next.length > MAX_RECIPIENTS) {
      setRefused("too_many");
      return;
    }
    onChange(next);
    setDraft("");
    setRefused(null);
  }

  const refusal =
    refused === "too_many"
      ? t("mail.alerts.too_many", { max: MAX_RECIPIENTS })
      : refused && t("mail.alerts.not_an_address");

  return (
    <Stack spacing={1.5}>
      {value.length > 0 ? (
        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1} data-testid="mail-recipients">
          {value.map((address) => (
            <Chip
              key={address}
              label={address}
              dir="ltr"
              sx={{ maxWidth: "100%" }}
              onDelete={() => onChange(value.filter((item) => item !== address))}
              deleteIcon={<CancelIcon titleAccess={t("mail.alerts.remove", { address })} />}
            />
          ))}
        </Stack>
      ) : (
        <Typography variant="body2" color="text.secondary">
          {t("mail.alerts.none")}
        </Typography>
      )}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1} useFlexGap alignItems={{ xs: "stretch", sm: "flex-start" }}>
        <TextField
          size="small"
          fullWidth
          type="email"
          label={t("mail.alerts.address")}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setRefused(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
          onBlur={add}
          error={!!refusal || !!error}
          helperText={refusal || (error ?? t("mail.alerts.address_help"))}
          slotProps={{ htmlInput: { spellCheck: false, autoComplete: "off" } }}
        />
        <Button variant="outlined" startIcon={<AddIcon />} onClick={add} sx={{ flexShrink: 0, minHeight: 40 }}>
          {t("mail.alerts.add")}
        </Button>
      </Stack>
    </Stack>
  );
}
