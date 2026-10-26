/**
 * Adding or changing a sign-in provider: what it is (OIDC, SAML, a directory), how this server reaches
 * it, and what happens to the people who sign in with it. The module checks every setting; what it
 * refuses is shown here as it said it.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Checkbox from "@mui/material/Checkbox";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import type { Group, RbacRole } from "~/api/admin";
import { ProviderApiError, signInProviders, type Protocol, type SignInProvider } from "~/api/signInProviders";
import { FormDialog } from "~/components/FormDialog";

import { Address } from "./Address";
import {
  addresses,
  blankForm,
  formFromProvider,
  keyFromName,
  plainLdap,
  plainServerAddress,
  problems,
  toWrite,
  type ProviderForm,
} from "./model";
import { PolicyFields } from "./PolicyFields";

const PROTOCOLS: Protocol[] = ["oidc", "saml", "ldap"];

/**
 * What the provider is told about this server: where people open it (editable), and the addresses made from
 * it, to copy into the provider: its callback (OIDC), or its metadata, entity ID and ACS (SAML).
 */
function Registration({ form, set }: { form: ProviderForm; set: (change: Partial<ProviderForm>) => void }) {
  const { t } = useTranslation();
  if (form.protocol === "ldap") return null;
  const here = addresses(form.key, form.serverAddress);
  return (
    <Stack spacing={1.5}>
      <TextField
        size="small"
        required
        label={t("auth_providers.server_address")}
        value={form.serverAddress}
        onChange={(e) => set({ serverAddress: e.target.value })}
        helperText={t("auth_providers.server_address_help")}
        placeholder="https://ai.example.internal"
        slotProps={{ htmlInput: { spellCheck: false } }}
      />
      {plainServerAddress(form) && <Alert severity="warning">{t("auth_providers.server_address_plain")}</Alert>}
      {form.protocol === "oidc" ? (
        <>
          <Typography variant="body2" color="text.secondary">{t("auth_providers.oidc.register")}</Typography>
          <Address label={t("auth_providers.oidc.redirect_uri")} value={here.callback} />
        </>
      ) : (
        <>
          <Typography variant="body2" color="text.secondary">{t("auth_providers.saml.register")}</Typography>
          <Address label={t("auth_providers.saml.metadata_url")} value={here.metadata} />
          <TextField
            size="small"
            fullWidth
            label={t("auth_providers.saml.entity_id")}
            value={form.spEntityId}
            onChange={(e) => set({ spEntityId: e.target.value })}
            placeholder={here.metadata}
            helperText={t("auth_providers.saml.entity_id_help")}
            slotProps={{ htmlInput: { spellCheck: false, maxLength: 256 } }}
          />
          <Address label={t("auth_providers.saml.acs_url")} value={here.acs} />
          <Address label={t("auth_providers.saml.sls_url")} value={here.sls} />
        </>
      )}
    </Stack>
  );
}

function SecretField({
  label,
  value,
  onChange,
  isSet,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  isSet: boolean;
}) {
  const { t } = useTranslation();
  return (
    <TextField
      size="small"
      type="password"
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      required={!isSet}
      autoComplete="new-password"
      helperText={isSet ? t("auth_providers.keep_secret") : undefined}
    />
  );
}

function ProtocolFields({
  form,
  set,
  stored,
}: {
  form: ProviderForm;
  set: (change: Partial<ProviderForm>) => void;
  stored: SignInProvider | null;
}) {
  const { t } = useTranslation();
  const secretSet = (name: string) => !!stored?.secrets_set.includes(name);

  if (form.protocol === "oidc") {
    return (
      <Stack spacing={2}>
        <TextField
          size="small"
          required
          label={t("auth_providers.oidc.issuer")}
          value={form.issuer}
          onChange={(e) => set({ issuer: e.target.value })}
          placeholder="https://login.microsoftonline.com/<tenant>/v2.0"
          helperText={t("auth_providers.oidc.issuer_help")}
        />
        <TextField
          size="small"
          required
          label={t("auth_providers.oidc.client_id")}
          value={form.clientId}
          onChange={(e) => set({ clientId: e.target.value })}
        />
        <SecretField
          label={t("auth_providers.oidc.client_secret")}
          value={form.clientSecret}
          onChange={(clientSecret) => set({ clientSecret })}
          isSet={secretSet("client_secret")}
        />
        <TextField
          size="small"
          label={t("auth_providers.oidc.scopes")}
          value={form.scopes}
          onChange={(e) => set({ scopes: e.target.value })}
          helperText={t("auth_providers.oidc.scopes_help")}
        />
        <TextField
          size="small"
          label={t("auth_providers.oidc.subject_claim")}
          value={form.subjectClaim}
          onChange={(e) => set({ subjectClaim: e.target.value })}
          helperText={t("auth_providers.oidc.subject_claim_help")}
          slotProps={{ htmlInput: { spellCheck: false, maxLength: 256 } }}
        />
        <TextField
          select
          size="small"
          label={t("auth_providers.oidc.userinfo")}
          value={form.userinfo}
          onChange={(e) => set({ userinfo: e.target.value as ProviderForm["userinfo"] })}
        >
          {(["when_missing", "always", "never"] as const).map((value) => (
            <MenuItem key={value} value={value}>
              {t(`auth_providers.oidc.userinfo_${value}`)}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
    );
  }

  if (form.protocol === "saml") {
    return (
      <Stack spacing={2}>
        <TextField
          select
          size="small"
          label={t("auth_providers.saml.metadata_source")}
          value={form.metadataSource}
          onChange={(e) => set({ metadataSource: e.target.value as ProviderForm["metadataSource"] })}
        >
          <MenuItem value="url">{t("auth_providers.saml.metadata_from_url")}</MenuItem>
          <MenuItem value="xml">{t("auth_providers.saml.metadata_pasted")}</MenuItem>
          <MenuItem value="mdq">{t("auth_providers.saml.metadata_mdq")}</MenuItem>
        </TextField>
        {form.metadataSource === "mdq" ? (
          <>
            <TextField
              size="small"
              required
              label={t("auth_providers.saml.mdq_url")}
              value={form.idpMdqUrl}
              onChange={(e) => set({ idpMdqUrl: e.target.value })}
              helperText={t("auth_providers.saml.mdq_url_help")}
              placeholder="https://mdq.example.org"
              slotProps={{ htmlInput: { spellCheck: false, maxLength: 1024 } }}
            />
            <TextField
              size="small"
              required
              label={t("auth_providers.saml.idp_entity_id")}
              value={form.idpEntityId}
              onChange={(e) => set({ idpEntityId: e.target.value })}
              slotProps={{ htmlInput: { spellCheck: false, maxLength: 256 } }}
            />
          </>
        ) : form.metadataSource === "url" ? (
          <TextField
            size="small"
            required
            label={t("auth_providers.saml.idp_metadata_url")}
            value={form.idpMetadataUrl}
            onChange={(e) => set({ idpMetadataUrl: e.target.value })}
            placeholder="https://login.microsoftonline.com/<tenant>/federationmetadata/2007-06/federationmetadata.xml"
          />
        ) : (
          <TextField
            size="small"
            required
            multiline
            minRows={4}
            maxRows={10}
            label={t("auth_providers.saml.idp_metadata_xml")}
            value={form.idpMetadataXml}
            onChange={(e) => set({ idpMetadataXml: e.target.value })}
            slotProps={{ htmlInput: { spellCheck: false, style: { fontFamily: "monospace", fontSize: 12 } } }}
          />
        )}
        <FormControlLabel
          control={
            <Checkbox checked={form.emailVerifiedByIdp} onChange={(e) => set({ emailVerifiedByIdp: e.target.checked })} />
          }
          label={t("auth_providers.email_verified_by_idp")}
        />
        <TextField
          size="small"
          required={form.metadataSource === "mdq"}
          multiline
          minRows={2}
          maxRows={8}
          label={t("auth_providers.saml.signing_certificate")}
          value={form.metadataSigningCert}
          onChange={(e) => set({ metadataSigningCert: e.target.value })}
          helperText={t("auth_providers.saml.signing_certificate_help")}
          placeholder="-----BEGIN CERTIFICATE-----"
          slotProps={{
            htmlInput: { spellCheck: false, maxLength: 20000, style: { fontFamily: "monospace", fontSize: 12 } },
          }}
        />
        <FormControlLabel
          control={<Checkbox checked={form.signRequests} onChange={(e) => set({ signRequests: e.target.checked })} />}
          label={t("auth_providers.saml.sign_requests")}
        />
        <FormControlLabel
          control={<Checkbox checked={form.logoutAtIdp} onChange={(e) => set({ logoutAtIdp: e.target.checked })} />}
          label={t("auth_providers.saml.logout_at_idp")}
        />
        <FormControlLabel
          control={
            <Checkbox checked={form.logoutFromIdp} onChange={(e) => set({ logoutFromIdp: e.target.checked })} />
          }
          label={t("auth_providers.saml.logout_from_idp")}
        />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <TextField
        size="small"
        required
        label={t("auth_providers.ldap.url")}
        value={form.ldapUrl}
        onChange={(e) => set({ ldapUrl: e.target.value })}
        placeholder="ldaps://dc1.example.com:636"
      />
      <FormControlLabel
        control={<Checkbox checked={form.startTls} onChange={(e) => set({ startTls: e.target.checked })} />}
        label={t("auth_providers.ldap.start_tls")}
      />
      {plainLdap(form) && <Alert severity="warning">{t("auth_providers.ldap.plain_warning")}</Alert>}
      <TextField
        select
        size="small"
        label={t("auth_providers.ldap.preset")}
        value={form.preset}
        onChange={(e) => set({ preset: e.target.value as ProviderForm["preset"] })}
      >
        <MenuItem value="ad">{t("auth_providers.ldap.preset_ad")}</MenuItem>
        <MenuItem value="generic">{t("auth_providers.ldap.preset_generic")}</MenuItem>
      </TextField>
      <TextField
        size="small"
        required
        label={t("auth_providers.ldap.bind_dn")}
        value={form.bindDn}
        onChange={(e) => set({ bindDn: e.target.value })}
        placeholder="svc-fadenstack@example.com"
        helperText={t("auth_providers.ldap.bind_dn_help")}
      />
      <SecretField
        label={t("auth_providers.ldap.bind_password")}
        value={form.bindPassword}
        onChange={(bindPassword) => set({ bindPassword })}
        isSet={secretSet("bind_password")}
      />
      <TextField
        size="small"
        required
        label={t("auth_providers.ldap.base_dn")}
        value={form.baseDn}
        onChange={(e) => set({ baseDn: e.target.value })}
        placeholder="OU=Staff,DC=example,DC=com"
      />
      <TextField
        size="small"
        label={t("auth_providers.ldap.user_filter")}
        value={form.userFilter}
        onChange={(e) => set({ userFilter: e.target.value })}
        helperText={t("auth_providers.ldap.user_filter_help")}
        slotProps={{ htmlInput: { spellCheck: false, style: { fontFamily: "monospace", fontSize: 13 } } }}
      />
      <TextField
        size="small"
        label={t("auth_providers.ldap.subject_attribute")}
        value={form.subjectAttribute}
        onChange={(e) => set({ subjectAttribute: e.target.value })}
        helperText={t("auth_providers.ldap.subject_attribute_help")}
        slotProps={{ htmlInput: { spellCheck: false, maxLength: 128 } }}
      />
      <TextField
        size="small"
        multiline
        minRows={2}
        maxRows={8}
        label={t("auth_providers.ldap.ca_bundle")}
        value={form.caBundle}
        onChange={(e) => set({ caBundle: e.target.value })}
        helperText={t("auth_providers.ldap.ca_bundle_help")}
        placeholder="-----BEGIN CERTIFICATE-----"
        slotProps={{ htmlInput: { spellCheck: false, style: { fontFamily: "monospace", fontSize: 12 } } }}
      />
      <FormControlLabel
        control={
          <Checkbox checked={form.emailVerifiedByIdp} onChange={(e) => set({ emailVerifiedByIdp: e.target.checked })} />
        }
        label={t("auth_providers.email_verified_by_idp")}
      />
      <FormControlLabel
        control={<Checkbox checked={form.checkPeople} onChange={(e) => set({ checkPeople: e.target.checked })} />}
        label={t("auth_providers.ldap.check_people")}
      />
    </Stack>
  );
}

export function ProviderDialog({
  open,
  stored,
  roles,
  groups,
  provisioning = false,
  onClose,
  onSaved,
}: {
  open: boolean;
  /** The provider being changed; ``null`` adds one. */
  stored: SignInProvider | null;
  roles: RbacRole[];
  groups: Group[];
  /** The server offers provisioning (SCIM). */
  provisioning?: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const [form, setForm] = useState<ProviderForm>(() => blankForm("oidc", origin));
  const [keyTouched, setKeyTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    // A new provider's people get no roles unless some are named: the chat role is named to begin with.
    const chat = roles.find((role) => role.name === "default_user");
    setForm(stored ? formFromProvider(stored) : { ...blankForm("oidc", origin), defaultRoleIds: chat ? [chat.id] : [] });
    setKeyTouched(false);
    setRefused([]);
  }, [open, stored, origin, roles]);

  const set = (change: Partial<ProviderForm>) =>
    setForm((current) => {
      const next = { ...current, ...change };
      if (!stored && !keyTouched && change.name !== undefined) next.key = keyFromName(change.name);
      return next;
    });
  const missing = problems(form, !!stored);

  async function save() {
    setSaving(true);
    setRefused([]);
    try {
      const write = toWrite(form, stored ?? undefined);
      if (stored) await signInProviders.update(stored.key, write);
      else await signInProviders.create(write);
      onSaved();
    } catch (err: unknown) {
      setRefused(err instanceof ProviderApiError && err.messages.length ? err.messages : [t("auth_providers.failed_save")]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <FormDialog
      open={open}
      maxWidth="md"
      title={stored ? t("auth_providers.edit_title", { name: stored.name }) : t("auth_providers.create_title")}
      loading={saving}
      submitLabel={stored ? t("common.save") : t("auth_providers.create")}
      cancelLabel={t("common.cancel")}
      submitDisabled={missing.length > 0}
      onSubmit={save}
      onClose={onClose}
    >
      <Stack spacing={2} sx={{ mt: 1 }} data-testid="provider-dialog">
        {!stored && (
          <TextField
            select
            size="small"
            label={t("auth_providers.protocol")}
            value={form.protocol}
            onChange={(e) => set({ protocol: e.target.value as Protocol })}
            helperText={t(`auth_providers.protocol_help.${form.protocol}`)}
          >
            {PROTOCOLS.map((protocol) => (
              <MenuItem key={protocol} value={protocol}>
                {t(`auth_providers.protocols.${protocol}`)}
              </MenuItem>
            ))}
          </TextField>
        )}
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <TextField
            size="small"
            required
            label={t("auth_providers.name")}
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            helperText={t("auth_providers.name_help")}
            sx={{ flex: 1 }}
          />
          <TextField
            size="small"
            required
            label={t("auth_providers.key")}
            value={form.key}
            disabled={!!stored}
            onChange={(e) => {
              setKeyTouched(true);
              set({ key: e.target.value.toLowerCase() });
            }}
            helperText={t("auth_providers.key_help")}
            slotProps={{ htmlInput: { spellCheck: false, maxLength: 64 } }}
            sx={{ flex: 1 }}
          />
        </Stack>
        <FormControlLabel
          control={<Switch checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />}
          label={t("auth_providers.enabled_label")}
        />
        <Registration form={form} set={set} />
        <ProtocolFields form={form} set={set} stored={stored} />
        <Divider />
        <PolicyFields form={form} set={set} roles={roles} groups={groups} provisioning={provisioning} />
        {missing.length > 0 && (
          <Typography variant="caption" color="text.secondary" data-testid="provider-missing">
            {t("auth_providers.still_needed", {
              fields: missing.map((key) => t(`auth_providers.problem.${key}`)).join(", "),
            })}
          </Typography>
        )}
        {refused.length > 0 && (
          <Alert severity="error" data-testid="provider-refused">
            {refused.length === 1 ? (
              refused[0]
            ) : (
              <ul style={{ margin: 0, paddingInlineStart: 18 }}>
                {refused.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            )}
          </Alert>
        )}
      </Stack>
    </FormDialog>
  );
}
