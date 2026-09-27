/**
 * Create a skill -- from the chat, where the chat's model can draft it from
 * the conversation, or from scratch.
 *
 * The model only proposes: the draft is shown in the editor, and nothing is
 * saved until the user presses Save.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";

import {
  createSkill,
  draftSkill,
  type SkillActivation,
  type SkillDetail,
  type SkillVisibility,
} from "~/api/skills";
import {
  EMPTY_SKILL_FORM,
  SkillActivationField,
  SkillInstructionsFields,
  type SkillFormValues,
} from "./SkillForm";

export interface CreateSkillDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (skill: SkillDetail, useNow: boolean) => void;
  /** Draft from this chat: its session and the model to write with. */
  sessionId?: string | null;
  model?: string;
  /** What the user typed after ``/skill``. */
  initialGuidance?: string;
  /** Offer "use it in this chat now". */
  inChat?: boolean;
  /** Administrators create skills the organization owns. */
  organization?: boolean;
  /** May make it visible to the whole organization. */
  canPublishOrg?: boolean;
}

/** A skill name from a title: "Code Review!" -> "code-review". */
export function slugFromTitle(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 64)
      .replace(/-+$/, "") || ""
  );
}

export default function CreateSkillDialog({
  open,
  onClose,
  onCreated,
  sessionId,
  model,
  initialGuidance,
  inChat,
  organization,
  canPublishOrg,
}: CreateSkillDialogProps) {
  const { t } = useTranslation();
  const canDraft = Boolean(sessionId && model);
  const [step, setStep] = useState<"ask" | "edit">(canDraft ? "ask" : "edit");
  const [guidance, setGuidance] = useState("");
  const [values, setValues] = useState<SkillFormValues>(EMPTY_SKILL_FORM);
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [activation, setActivation] = useState<SkillActivation>(organization ? "auto" : "manual");
  const [visibility, setVisibility] = useState<SkillVisibility>(organization ? "tenant" : "private");
  const [useNow, setUseNow] = useState(true);
  const [drafting, setDrafting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Start over each time it opens.
  useEffect(() => {
    if (!open) return;
    setStep(canDraft ? "ask" : "edit");
    setGuidance(initialGuidance ?? "");
    setValues(EMPTY_SKILL_FORM);
    setSlug("");
    setSlugTouched(false);
    setActivation(organization ? "auto" : "manual");
    setVisibility(organization ? "tenant" : "private");
    setUseNow(true);
    setError(null);
  }, [open, canDraft, initialGuidance, organization]);

  const onChange = <K extends keyof SkillFormValues>(key: K, value: SkillFormValues[K]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    if (key === "name" && !slugTouched) setSlug(slugFromTitle(String(value)));
  };

  async function draft() {
    if (!sessionId || !model) return;
    setDrafting(true);
    setError(null);
    try {
      const proposed = await draftSkill({
        model,
        session_id: sessionId,
        guidance: guidance.trim() || undefined,
      });
      setValues({
        ...EMPTY_SKILL_FORM,
        name: proposed.name,
        description: proposed.description,
        body: proposed.body_markdown,
      });
      setSlug(proposed.slug);
      setSlugTouched(true);
      setStep("edit");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDrafting(false);
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const skill = await createSkill({
        name: values.name.trim(),
        slug: slug || undefined,
        description: values.description.trim(),
        body_markdown: values.body,
        activation,
        owner: organization ? "organization" : "me",
        visibility,
        publish: true,
        change_note: canDraft ? t("skills.created_from_chat") : undefined,
      });
      onCreated(skill, Boolean(inChat && useNow));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const slugValid = !slug || /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug);
  const orgVisible = visibility !== "private";

  return (
    <Dialog open={open} onClose={saving || drafting ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("skills.create_title")}</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {step === "ask" && (
          <Stack spacing={2}>
            <Typography variant="body2" color="text.secondary">
              {t("skills.draft_intro")}
            </Typography>
            <TextField
              label={t("skills.draft_guidance")}
              placeholder={t("skills.draft_guidance_placeholder")}
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
              multiline
              minRows={2}
              fullWidth
              autoFocus
              slotProps={{ htmlInput: { maxLength: 2000 } }}
            />
            <Typography variant="caption" color="text.secondary">
              {t("skills.draft_privacy_hint")}
            </Typography>
          </Stack>
        )}

        {step === "edit" && (
          <Stack spacing={2}>
            {canDraft && (
              <Alert severity="info" icon={<AutoAwesomeIcon />}>
                {t("skills.draft_review_hint")}
              </Alert>
            )}
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label={t("skills.skill_name")}
                value={values.name}
                onChange={(e) => onChange("name", e.target.value)}
                required
                fullWidth
                autoFocus={!canDraft}
                slotProps={{ htmlInput: { maxLength: 255 } }}
              />
              <TextField
                label={t("skills.slug")}
                value={slug}
                onChange={(e) => {
                  setSlug(e.target.value.toLowerCase());
                  setSlugTouched(true);
                }}
                error={!slugValid}
                helperText={slugValid ? t("skills.slug_help", { slug: slug || "…" }) : t("skills.slug_invalid")}
                sx={{ minWidth: 240 }}
                slotProps={{ htmlInput: { maxLength: 64, spellCheck: false } }}
              />
            </Stack>
            <SkillInstructionsFields values={values} onChange={onChange} minRows={10} />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <SkillActivationField
                value={activation}
                onChange={setActivation}
                allowAlways={!orgVisible}
              />
              {organization && (
                <TextField
                  select
                  label={t("skills.visibility")}
                  value={visibility}
                  onChange={(e) => {
                    const next = e.target.value as SkillVisibility;
                    setVisibility(next);
                    if (next !== "private" && activation === "always") setActivation("auto");
                  }}
                  fullWidth
                  helperText={t(`skills.visibility_${visibility}_hint`)}
                >
                  <MenuItem value="private">{t("skills.visibility_private")}</MenuItem>
                  <MenuItem value="tenant" disabled={!canPublishOrg}>
                    {t("skills.visibility_tenant")}
                  </MenuItem>
                  <MenuItem value="global" disabled={!canPublishOrg}>
                    {t("skills.visibility_global")}
                  </MenuItem>
                </TextField>
              )}
            </Stack>
            {inChat && (
              <FormControlLabel
                control={<Checkbox checked={useNow} onChange={(e) => setUseNow(e.target.checked)} />}
                label={t("skills.use_now")}
              />
            )}
            {!organization && (
              <Typography variant="caption" color="text.secondary">
                {t("skills.private_by_default")}
              </Typography>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving || drafting}>
          {t("common.cancel")}
        </Button>
        {step === "ask" && (
          <>
            <Button onClick={() => setStep("edit")} disabled={drafting}>
              {t("skills.draft_write_myself")}
            </Button>
            <Button
              variant="contained"
              startIcon={drafting ? <CircularProgress size={16} /> : <AutoAwesomeIcon />}
              onClick={() => void draft()}
              disabled={drafting}
            >
              {drafting ? t("skills.drafting") : t("skills.draft_from_chat")}
            </Button>
          </>
        )}
        {step === "edit" && (
          <Box>
            <Button
              variant="contained"
              onClick={() => void save()}
              disabled={saving || !values.name.trim() || !values.body.trim() || !slugValid}
            >
              {saving ? t("common.saving") : t("skills.create_save")}
            </Button>
          </Box>
        )}
      </DialogActions>
    </Dialog>
  );
}
