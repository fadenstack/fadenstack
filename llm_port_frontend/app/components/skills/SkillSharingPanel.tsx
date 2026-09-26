/**
 * Who sees a skill: private (its owner and the people and groups it is
 * shared with), the whole organization, or every tenant.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DeleteIcon from "@mui/icons-material/Delete";
import GroupIcon from "@mui/icons-material/Group";
import PersonIcon from "@mui/icons-material/Person";

import {
  findPrincipals,
  namePrincipals,
  setGrants,
  setVisibility,
  type GrantAccess,
  type PrincipalOption,
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
  const [options, setOptions] = useState<PrincipalOption[]>([]);
  const [query, setQuery] = useState("");
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
        const byKey = new Map(names.map((n) => [`${n.type}:${n.id}`, n.label]));
        setRows(
          grants.map((g) => ({
            ...g,
            label:
              byKey.get(`${g.principal_type}:${g.principal_id}`) ??
              t("skills.share_unknown_principal"),
          })),
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [skill, canShare, t]);

  // Search as the user types.
  useEffect(() => {
    if (!canShare || query.trim().length < 2) {
      setOptions([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      findPrincipals(query.trim())
        .then((found) => {
          if (!cancelled) setOptions(found);
        })
        .catch(() => {});
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, canShare]);

  const taken = useMemo(
    () => new Set(rows.map((r) => `${r.principal_type}:${r.principal_id}`)),
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
          <Autocomplete<PrincipalOption>
            options={options.filter((o) => !taken.has(`${o.type}:${o.id}`))}
            getOptionLabel={(o) => o.label}
            filterOptions={(x) => x}
            inputValue={query}
            onInputChange={(_, v) => setQuery(v)}
            value={null}
            onChange={(_, picked) => {
              if (!picked) return;
              setRows((prev) => [
                ...prev,
                {
                  principal_type: picked.type,
                  principal_id: picked.id,
                  access: "read",
                  label: picked.label,
                },
              ]);
              setQuery("");
            }}
            renderOption={(props, o) => (
              <li {...props} key={`${o.type}:${o.id}`}>
                <ListItemIcon sx={{ minWidth: 32 }}>
                  {o.type === "group" ? <GroupIcon fontSize="small" /> : <PersonIcon fontSize="small" />}
                </ListItemIcon>
                {o.label}
              </li>
            )}
            noOptionsText={
              query.trim().length < 2 ? t("skills.share_search_hint") : t("skills.share_no_match")
            }
            renderInput={(params) => (
              <TextField {...params} size="small" placeholder={t("skills.share_search")} />
            )}
          />
        ) : (
          <Alert severity="info">{t("skills.share_not_allowed")}</Alert>
        )}
        <List dense>
          {rows.map((row, i) => (
            <ListItem
              key={`${row.principal_type}:${row.principal_id}`}
              divider
              secondaryAction={
                canShare && (
                  <IconButton
                    edge="end"
                    size="small"
                    aria-label={t("common.delete")}
                    onClick={() => setRows((prev) => prev.filter((_, j) => j !== i))}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                )
              }
            >
              <ListItemIcon sx={{ minWidth: 32 }}>
                {row.principal_type === "group" ? (
                  <GroupIcon fontSize="small" />
                ) : (
                  <PersonIcon fontSize="small" />
                )}
              </ListItemIcon>
              <ListItemText primary={row.label} />
              <TextField
                select
                size="small"
                value={row.access}
                disabled={!canShare}
                onChange={(e) =>
                  setRows((prev) =>
                    prev.map((r, j) =>
                      j === i ? { ...r, access: e.target.value as GrantAccess } : r,
                    ),
                  )
                }
                sx={{ mr: 5, minWidth: 130 }}
              >
                <MenuItem value="read">{t("skills.access_read")}</MenuItem>
                <MenuItem value="write">{t("skills.access_write")}</MenuItem>
              </TextField>
            </ListItem>
          ))}
          {rows.length === 0 && (
            <ListItem>
              <ListItemText secondary={t("skills.share_nobody")} />
            </ListItem>
          )}
        </List>
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
