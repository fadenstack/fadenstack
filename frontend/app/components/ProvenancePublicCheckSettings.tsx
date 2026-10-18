/**
 * The public check page's settings, in the provenance module's card
 * (Settings > Modules): whether /check is offered -- pictures and files
 * checked without an account -- and whom the page names for text checks,
 * which are for people with an account.
 *
 * Both are system settings that apply at once (provenance.public_check_*);
 * the switch saves when it is flipped, the contact with its button.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { systemSettingsApi } from "~/api/systemSettings";

export const PUBLIC_CHECK_ENABLED_KEY = "provenance.public_check_enabled";
export const PUBLIC_CHECK_CONTACT_KEY = "provenance.public_check_contact";
const MAX_CONTACT = 500;

export default function ProvenancePublicCheckSettings() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [contact, setContact] = useState("");
  const [savedContact, setSavedContact] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<"enabled" | "contact" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    systemSettingsApi
      .values()
      .then(({ items }) => {
        if (cancelled) return;
        setEnabled(items[PUBLIC_CHECK_ENABLED_KEY]?.value === true);
        const stored = items[PUBLIC_CHECK_CONTACT_KEY]?.value;
        setContact(typeof stored === "string" ? stored : "");
        setSavedContact(typeof stored === "string" ? stored : "");
      })
      .catch(() => {
        if (!cancelled) setError(t("content_check.settings.load_failed"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  async function save(which: "enabled" | "contact", value: boolean | string) {
    setSaving(which);
    setError(null);
    setSaved(null);
    try {
      await systemSettingsApi.update(
        which === "enabled" ? PUBLIC_CHECK_ENABLED_KEY : PUBLIC_CHECK_CONTACT_KEY,
        value,
        "local",
      );
      if (which === "enabled") {
        setEnabled(value === true);
        setSaved(value === true ? t("content_check.settings.now_on") : t("content_check.settings.now_off"));
      } else {
        setSavedContact(String(value));
        setContact(String(value));
        setSaved(t("content_check.settings.contact_saved"));
      }
    } catch {
      setError(t("content_check.settings.save_failed"));
    } finally {
      setSaving(null);
    }
  }

  const pageUrl = typeof window === "undefined" ? "/check" : `${window.location.origin}/check`;

  return (
    <Box data-testid="provenance-public-check-settings">
      <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
        {t("content_check.settings.title")}
      </Typography>
      <Stack spacing={1.5}>
        <FormControlLabel
          disabled={loading || saving !== null}
          control={
            <Switch
              checked={enabled}
              onChange={(event) => void save("enabled", event.target.checked)}
              slotProps={{
                input: {
                  role: "switch",
                  "aria-label": t("content_check.settings.enabled"),
                },
              }}
            />
          }
          label={
            <Box>
              <Typography variant="body2">{t("content_check.settings.enabled")}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t("content_check.settings.enabled_help")}
              </Typography>
            </Box>
          }
        />
        {enabled && (
          <Typography variant="body2" data-testid="provenance-public-check-link">
            {t("content_check.settings.page_is_at")}{" "}
            <Link href="/check" target="_blank" rel="noopener" sx={{ wordBreak: "break-all" }}>
              {pageUrl}
            </Link>
          </Typography>
        )}
        <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
          <TextField
            size="small"
            fullWidth
            label={t("content_check.settings.contact")}
            helperText={t("content_check.settings.contact_help")}
            value={contact}
            disabled={loading}
            onChange={(event) => setContact(event.target.value.slice(0, MAX_CONTACT))}
          />
          <Button
            variant="contained"
            size="small"
            disabled={loading || saving !== null || contact.trim() === savedContact.trim()}
            onClick={() => void save("contact", contact.trim())}
            sx={{ alignSelf: { md: "flex-start" }, mt: { md: 0.25 } }}
          >
            {saving === "contact" ? t("common.loading") : t("common.save")}
          </Button>
        </Stack>
        {error && (
          <Alert severity="error" onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {saved && (
          <Alert severity="success" onClose={() => setSaved(null)}>
            {saved}
          </Alert>
        )}
      </Stack>
    </Box>
  );
}
