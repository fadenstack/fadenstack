/**
 * Who may sign in with a provider, and what they get: accounts, roles, domains, groups, lifetimes; with
 * provisioning offered, whether only the people it made may sign in.
 */
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";

import type { Group, RbacRole } from "~/api/admin";

import type { ProviderForm } from "./model";

export function PolicyFields({
  form,
  set,
  roles,
  groups,
  provisioning = false,
}: {
  form: ProviderForm;
  set: (change: Partial<ProviderForm>) => void;
  roles: RbacRole[];
  groups: Group[];
  /** The server offers provisioning (SCIM): its accounts get these roles too. */
  provisioning?: boolean;
}) {
  const { t } = useTranslation();
  const rules = form.groupRules;
  const provisions = provisioning && form.protocol !== "ldap";

  return (
    <Stack spacing={2}>
      <Typography variant="subtitle2" fontWeight={700}>
        {t("auth_providers.policy.title")}
      </Typography>
      <FormControlLabel
        control={<Checkbox checked={form.createAccount} onChange={(e) => set({ createAccount: e.target.checked })} />}
        label={t("auth_providers.policy.create_account")}
      />
      {provisions && (
        <>
          <FormControlLabel
            control={
              <Checkbox checked={form.provisionedOnly} onChange={(e) => set({ provisionedOnly: e.target.checked })} />
            }
            label={t("auth_providers.policy.provisioned_only")}
          />
          <Typography variant="caption" color="text.secondary" sx={{ mt: -1.5 }}>
            {t("auth_providers.policy.provisioned_only_help")}
          </Typography>
        </>
      )}
      {(form.createAccount || provisions) && (
        <TextField
          select
          size="small"
          label={t("auth_providers.policy.default_roles")}
          value={form.defaultRoleIds}
          slotProps={{
            select: {
              multiple: true,
              renderValue: (ids) =>
                (ids as string[]).map((id) => roles.find((role) => role.id === id)?.name ?? id).join(", "),
            },
          }}
          onChange={(e) => set({ defaultRoleIds: e.target.value as unknown as string[] })}
          helperText={t(
            provisions ? "auth_providers.policy.default_roles_help_provisioning" : "auth_providers.policy.default_roles_help",
          )}
        >
          {roles.map((role) => (
            <MenuItem key={role.id} value={role.id}>
              <Checkbox checked={form.defaultRoleIds.includes(role.id)} size="small" />
              <ListItemText primary={role.name} />
            </MenuItem>
          ))}
        </TextField>
      )}
      <TextField
        size="small"
        label={t("auth_providers.policy.allowed_domains")}
        value={form.allowedDomains}
        onChange={(e) => set({ allowedDomains: e.target.value })}
        helperText={t("auth_providers.policy.allowed_domains_help")}
        placeholder="example.com, example.de"
      />
      {form.allowedDomains.trim() && (
        <FormControlLabel
          control={<Checkbox checked={form.ssoOnly} onChange={(e) => set({ ssoOnly: e.target.checked })} />}
          label={t("auth_providers.policy.sso_only")}
        />
      )}
      <Stack spacing={1} data-testid="group-rules">
        <Typography variant="body2" fontWeight={600}>
          {t("auth_providers.policy.group_rules")}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {t("auth_providers.policy.group_rules_help")}
        </Typography>
        {rules.map((rule, index) => (
          <Stack key={index} direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
            <TextField
              size="small"
              label={t("auth_providers.policy.external_group")}
              value={rule.external}
              onChange={(e) =>
                set({ groupRules: rules.map((r, i) => (i === index ? { ...r, external: e.target.value } : r)) })
              }
              sx={{ flex: 1 }}
            />
            <TextField
              select
              size="small"
              label={t("auth_providers.policy.local_group")}
              value={rule.group_id}
              onChange={(e) =>
                set({ groupRules: rules.map((r, i) => (i === index ? { ...r, group_id: e.target.value } : r)) })
              }
              sx={{ flex: 1, minWidth: 160 }}
            >
              {groups.map((group) => (
                <MenuItem key={group.id} value={group.id}>
                  {group.name}
                </MenuItem>
              ))}
            </TextField>
            <IconButton
              aria-label={t("auth_providers.policy.remove_rule")}
              onClick={() => set({ groupRules: rules.filter((_, i) => i !== index) })}
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Stack>
        ))}
        <Button
          startIcon={<AddIcon />}
          size="small"
          sx={{ alignSelf: "flex-start" }}
          onClick={() => set({ groupRules: [...rules, { external: "", group_id: "" }] })}
        >
          {t("auth_providers.policy.add_rule")}
        </Button>
        <FormControlLabel
          control={<Checkbox checked={form.manageGroups} onChange={(e) => set({ manageGroups: e.target.checked })} />}
          label={t("auth_providers.policy.manage_groups")}
        />
        <Typography variant="caption" color="text.secondary" sx={{ mt: -1 }}>
          {t("auth_providers.policy.manage_groups_help")}
        </Typography>
      </Stack>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <TextField
          size="small"
          type="number"
          label={t("auth_providers.policy.session_hours")}
          value={form.sessionHours}
          onChange={(e) => set({ sessionHours: e.target.value })}
          slotProps={{ htmlInput: { min: 1, step: 1 } }}
          sx={{ flex: 1 }}
        />
        <TextField
          size="small"
          type="number"
          label={t("auth_providers.policy.inactive_days")}
          value={form.inactiveDays}
          onChange={(e) => set({ inactiveDays: e.target.value })}
          helperText={t("auth_providers.policy.inactive_days_help")}
          slotProps={{ htmlInput: { min: 1, step: 1 } }}
          sx={{ flex: 1 }}
        />
      </Stack>
    </Stack>
  );
}
