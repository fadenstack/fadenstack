/**
 * What an administrator set about marking a provider's chat answers (EU AI Act Art. 50(2)), on its card in the
 * security map. With the provenance module on, the gateway refuses a chat to a route whose answers would carry
 * no mark, unless the provider marks its text itself (the evidence for it) or may answer unmarked (a reason).
 * Who and when come from the server.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import Link from "@mui/material/Link";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import type { Provider, TextMarkingBody } from "~/api/llm";
import { EXTERNAL_LINK } from "~/lib/vendorLinks";

type Choice = "none" | "upstream" | "allow";

/** The shortest reason the server takes. */
const MIN_REASON = 10;

function initial(provider: Provider): Choice {
  if (provider.text_marking?.upstream) return "upstream";
  if (provider.text_marking?.allow_unmarked) return "allow";
  return "none";
}

/** The status line: what the administrator set, or what happens without it. */
function status(provider: Provider, remote: boolean, t: (key: string, opts?: Record<string, unknown>) => string) {
  const upstream = provider.text_marking?.upstream;
  if (upstream) return t("text_marking.status_upstream", { date: upstream.evidence_date });
  const allowed = provider.text_marking?.allow_unmarked;
  if (allowed) return t("text_marking.status_allowed", { reason: allowed.reason });
  return t(remote ? "text_marking.status_none_remote" : "text_marking.status_none_local");
}

export function TextMarkingControl({
  provider,
  onSave,
}: {
  provider: Provider;
  /** Present when the viewer may change it. */
  onSave?: (provider: Provider, body: TextMarkingBody) => Promise<void>;
}) {
  const { t } = useTranslation();
  // A remote API can mark its own text; a runtime of ours marks it with the watermark, or not at all.
  const remote = provider.type === "cloud";
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<Choice>(initial(provider));
  const [vendor, setVendor] = useState(provider.text_marking?.upstream?.vendor ?? "");
  const [url, setUrl] = useState(provider.text_marking?.upstream?.evidence_url ?? "");
  const [date, setDate] = useState(provider.text_marking?.upstream?.evidence_date ?? "");
  const [reason, setReason] = useState(provider.text_marking?.allow_unmarked?.reason ?? "");
  const [saving, setSaving] = useState(false);

  const valid =
    choice === "none" ||
    (choice === "upstream" && /^https:\/\/\S+$/.test(url) && /^\d{4}-\d{2}-\d{2}$/.test(date)) ||
    (choice === "allow" && reason.trim().length >= MIN_REASON);

  async function save() {
    if (!onSave || !valid) return;
    setSaving(true);
    try {
      const body: TextMarkingBody = {};
      if (choice === "upstream") body.upstream = { evidence_url: url, evidence_date: date, ...(vendor ? { vendor } : {}) };
      if (choice === "allow") body.allow_unmarked = { reason: reason.trim() };
      await onSave(provider, body);
      setOpen(false);
    } finally {
      setSaving(false);
    }
  }

  const unmarkedAllowed = Boolean(provider.text_marking?.allow_unmarked);
  const evidence = provider.text_marking?.upstream?.evidence_url;
  return (
    <Stack direction="row" spacing={1} alignItems="baseline" sx={{ mt: 0.5, ml: 3.5 }} data-testid={`text-marking-${provider.id}`}>
      <Typography variant="caption" color={unmarkedAllowed ? "warning.main" : "text.secondary"} dir="auto">
        {status(provider, remote, t)}
        {evidence && (
          <>
            {" "}
            <Link href={evidence} {...EXTERNAL_LINK} variant="caption">
              {t("text_marking.evidence_link")}
            </Link>
          </>
        )}
      </Typography>
      {onSave && (
        <Button size="small" onClick={() => setOpen(true)} data-testid={`text-marking-edit-${provider.id}`}>
          {t("text_marking.edit")}
        </Button>
      )}
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t("text_marking.title", { name: provider.name })}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            {t("text_marking.intro")}
          </Typography>
          <RadioGroup value={choice} onChange={(e) => setChoice(e.target.value as Choice)}>
            <FormControlLabel value="none" control={<Radio />} label={t("text_marking.choice_none")} />
            {remote && (
              <FormControlLabel value="upstream" control={<Radio />} label={t("text_marking.choice_upstream")} />
            )}
            {choice === "upstream" && (
              <Stack spacing={1.5} sx={{ ml: 4, my: 1 }}>
                <TextField
                  size="small"
                  label={t("text_marking.evidence_url")}
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://"
                  slotProps={{ htmlInput: { "data-testid": "text-marking-url" } }}
                />
                <TextField
                  size="small"
                  type="date"
                  label={t("text_marking.evidence_date")}
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  slotProps={{ inputLabel: { shrink: true } }}
                />
                <TextField
                  size="small"
                  label={t("text_marking.vendor")}
                  value={vendor}
                  onChange={(e) => setVendor(e.target.value)}
                />
              </Stack>
            )}
            <FormControlLabel value="allow" control={<Radio />} label={t("text_marking.choice_allow")} />
            {choice === "allow" && (
              <TextField
                size="small"
                multiline
                minRows={2}
                label={t("text_marking.reason")}
                helperText={t("text_marking.reason_help")}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                sx={{ ml: 4, my: 1 }}
                slotProps={{ htmlInput: { "data-testid": "text-marking-reason", maxLength: 500 } }}
              />
            )}
          </RadioGroup>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
          <Button variant="contained" onClick={save} disabled={!valid || saving} data-testid="text-marking-save">
            {t("common.save")}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
