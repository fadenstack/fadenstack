/**
 * Who sees a skill: private (its owner and the people and groups it is
 * shared with), the whole organization, or every tenant.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { PrincipalList } from "~/components/PrincipalList";
import { PrincipalPicker } from "~/components/PrincipalPicker";
import { principalKey } from "~/api/principals";
import {
  findPrincipals,
  namePrincipals,
  setGrants,
  setVisibility,
  type GrantAccess,
  type SkillDetail,
  type SkillGrant,
  type SkillVisibility,
} from "~/api/skills";

interface Props {
  skill: SkillDetail;
  /** RBAC: may share at all. */
  canShare: boolean;
  /** RBAC: may make a skill the whole organization sees. */
  canPublishOrg: boolean;
  isAdmin: boolean;
  onChanged: (skill: SkillDetail) => void;
}

type Row = SkillGrant & { label: string };

export default function SkillSharingPanel({
  skill,
  canShare,
  canPublishOrg,
  isAdmin,
  onChanged,
}: Props) {
  const { t } = useTranslation();
  const [visibility, setVis] = useState<SkillVisibility>(skill.visibility);
  const [rows, setRows] = useState<Row[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Name the people and groups it is shared with.
  useEffect(() => {
    const grants = skill.grants ?? [];
    setVis(skill.visibility);
    setRows(grants.map((g) => ({ ...g, label: g.principal_id })));
    if (!canShare || grants.length === 0) return;
    let cancelled = false;
    namePrincipals(grants)
      .then((names) => {
        if (cancelled) return;
        const byKey = new Map(names.map((n) => [principalKey(n.type, n.id), n.label]));
        setRows(
          grants.map((g) => ({
            ...g,
            label:
              byKey.get(principalKey(g.principal_type, g.principal_id)) ??
              t("skills.share_unknown_principal"),
          })),
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [skill, canShare, t]);

  const taken = useMemo(
    () => new Set(rows.map((r) => principalKey(r.principal_type, r.principal_id))),
    [rows],
  );
  const grantsDirty =
    JSON.stringify(rows.map(({ principal_type, principal_id, access }) => [principal_type, principal_id, access])) !==
    JSON.stringify(
      (skill.grants ?? []).map(({ principal_type, principal_id, access }) => [
        principal_type,
        principal_id,
        access,
      ]),
    );
  const visibilityDirty = visibility !== skill.visibility;

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      let current = skill;
      if (visibilityDirty) current = await setVisibility(skill.id, visibility);
      if (grantsDirty) {
        const grants = await setGrants(
          skill.id,
          rows.map(({ principal_type, principal_id, access }) => ({
            principal_type,
            principal_id,
            access,
          })),
        );
        current = { ...current, grants };
      }
      onChanged(current);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const choices: { value: SkillVisibility; allowed: boolean }[] = [
    { value: "private", allowed: true },
    { value: "tenant", allowed: canPublishOrg || isAdmin },
    { value: "global", allowed: isAdmin },
  ];

  return (
    <Stack spacing={3} sx={{ maxWidth: 720 }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {saved && !error && (
        <Alert severity="success" onClose={() => setSaved(false)}>
          {t("skills.share_saved")}
        </Alert>
      )}

      <Box>
        <Typography variant="subtitle2">{t("skills.visibility")}</Typography>
        <RadioGroup value={visibility} onChange={(e) => setVis(e.target.value as SkillVisibility)}>
          {choices.map((c) => (
            <FormControlLabel
              key={c.value}
              value={c.value}
              disabled={!c.allowed && c.value !== skill.visibility}
              control={<Radio size="small" />}
              label={
                <Box>
                  <Typography variant="body2">{t(`skills.visibility_${c.value}`)}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {t(`skills.visibility_${c.value}_hint`)}
                  </Typography>
                </Box>
              }
              sx={{ alignItems: "flex-start", mb: 1, "& .MuiRadio-root": { pt: 0.25 } }}
            />
          ))}
        </RadioGroup>
      </Box>

      <Box>
        <Typography variant="subtitle2">{t("skills.share_with")}</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {t("skills.share_with_hint")}
        </Typography>
        {canShare ? (
          <PrincipalPicker
            search={findPrincipals}
            exclude={taken}
            placeholder={t("skills.share_search")}
            onPick={(picked) =>
              setRows((prev) => [
                ...prev,
                { principal_type: picked.type, principal_id: picked.id, access: "read", label: picked.label },
              ])
            }
          />
        ) : (
          <Alert severity="info">{t("skills.share_not_allowed")}</Alert>
        )}
        <PrincipalList
          items={rows.map((r) => ({ type: r.principal_type, id: r.principal_id, label: r.label, access: r.access }))}
          emptyText={t("skills.share_nobody")}
          onRemove={
            canShare
              ? (item) =>
                  setRows((prev) =>
                    prev.filter((r) => principalKey(r.principal_type, r.principal_id) !== principalKey(item.type, item.id)),
                  )
              : undefined
          }
          renderControl={(item) => (
            <TextField
              select
              size="small"
              value={item.access}
              disabled={!canShare}
              onChange={(e) =>
                setRows((prev) =>
                  prev.map((r) =>
                    r.principal_type === item.type && r.principal_id === item.id
                      ? { ...r, access: e.target.value as GrantAccess }
                      : r,
                  ),
                )
              }
              sx={{ minWidth: 130 }}
            >
              <MenuItem value="read">{t("skills.access_read")}</MenuItem>
              <MenuItem value="write">{t("skills.access_write")}</MenuItem>
            </TextField>
          )}
        />
      </Box>

      <Box>
        <Button
          variant="contained"
          onClick={() => void save()}
          disabled={saving || (!grantsDirty && !visibilityDirty)}
        >
          {saving ? t("common.saving") : t("skills.share_save")}
        </Button>
      </Box>
    </Stack>
  );
}
