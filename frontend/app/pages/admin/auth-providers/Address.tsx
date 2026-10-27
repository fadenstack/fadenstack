/** A value to copy into the identity provider (an address, a token): read-only, with a copy button. */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";

export function Address({ label, value, helperText }: { label: string; value: string; helperText?: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <TextField
        size="small"
        fullWidth
        label={label}
        value={value}
        helperText={helperText}
        slotProps={{ input: { readOnly: true, sx: { fontFamily: "monospace", fontSize: 13 } } }}
      />
      <Tooltip title={copied ? t("auth_providers.copied") : t("auth_providers.copy")}>
        <IconButton
          aria-label={`${t("auth_providers.copy")}: ${label}`}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(true);
            } catch {
              // The browser refused; the value can still be selected and copied by hand.
            }
          }}
        >
          <ContentCopyIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
