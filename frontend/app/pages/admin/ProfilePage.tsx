/**
 * ProfilePage — self-service account management and API token generation.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CardHeader from "@mui/material/CardHeader";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import FormControl from "@mui/material/FormControl";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import KeyIcon from "@mui/icons-material/Key";
import LockResetIcon from "@mui/icons-material/LockReset";
import PaletteIcon from "@mui/icons-material/Palette";
import PersonIcon from "@mui/icons-material/Person";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";

import AppearanceSettings from "~/components/AppearanceSettings";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { adminUsers, type ApiTokenInfo } from "~/api/admin";
import { auth, type AuthUser } from "~/api/auth";

// ── Token expiry options ─────────────────────────────────────────────

const EXPIRY_OPTIONS: { key: string; value: number | null }[] = [
  { key: "profile.expiry_1h", value: 3600 },
  { key: "profile.expiry_24h", value: 86400 },
  { key: "profile.expiry_7d", value: 604800 },
  { key: "profile.expiry_30d", value: 2592000 },
  { key: "profile.expiry_90d", value: 7776000 },
  // Every token expires: a year is the longest the server allows (users.api_token_max_lifetime_s).
  { key: "profile.expiry_1y", value: 31536000 },
];

export default function ProfilePage() {
  const { t, i18n } = useTranslation();

  // ── User info ────────────────────────────────────────────────────
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── Password change ──────────────────────────────────────────────
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showCurrentPw, setShowCurrentPw] = useState(false);
  const [showNewPw, setShowNewPw] = useState(false);
  const [pwError, setPwError] = useState<string | null>(null);
  const [pwSuccess, setPwSuccess] = useState<string | null>(null);
  const [pwLoading, setPwLoading] = useState(false);

  // ── Token generation ─────────────────────────────────────────────
  const [tokenName, setTokenName] = useState("");
  const [tenantId, setTenantId] = useState("default");
  const [expiresIn, setExpiresIn] = useState<number | null>(86400);
  const [generatedToken, setGeneratedToken] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [tokenLoading, setTokenLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  // ── The user's tokens ────────────────────────────────────────────
  const [tokens, setTokens] = useState<ApiTokenInfo[] | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiTokenInfo | null>(null);
  const [revoking, setRevoking] = useState(false);

  function loadTokens() {
    adminUsers.listMyApiTokens().then(setTokens).catch(() => setTokens([]));
  }

  useEffect(() => {
    auth.me().then(setUser).catch((err) => setLoadError(err.message));
    loadTokens();
  }, []);

  async function handleRevoke() {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await adminUsers.revokeMyApiToken(revokeTarget.id);
      setRevokeTarget(null);
      loadTokens();
    } catch (err: unknown) {
      setTokenError(err instanceof Error ? err.message : t("profile.revoke_failed"));
      setRevokeTarget(null);
    } finally {
      setRevoking(false);
    }
  }

  const formatDate = (iso: string) => new Date(iso).toLocaleDateString(i18n.language || undefined);

  // ── Password change handler ──────────────────────────────────────

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwError(null);
    setPwSuccess(null);

    if (newPassword !== confirmPassword) {
      setPwError(t("profile.password_mismatch"));
      return;
    }
    if (newPassword.length < 6) {
      setPwError(t("profile.password_too_short"));
      return;
    }

    setPwLoading(true);
    try {
      await adminUsers.changePassword(currentPassword, newPassword);
      setPwSuccess(t("profile.password_changed"));
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err: unknown) {
      setPwError(err instanceof Error ? err.message : "Failed to change password.");
    } finally {
      setPwLoading(false);
    }
  }

  // ── Token generation handler ─────────────────────────────────────

  async function handleGenerateToken() {
    setTokenError(null);
    setGeneratedToken(null);
    setCopied(false);
    setTokenLoading(true);
    try {
      const result = await adminUsers.generateApiToken(tenantId, expiresIn ?? undefined, tokenName);
      setGeneratedToken(result.token);
      setTokenName("");
      loadTokens();
    } catch (err: unknown) {
      setTokenError(err instanceof Error ? err.message : "Failed to generate token.");
    } finally {
      setTokenLoading(false);
    }
  }

  async function handleCopyToken() {
    if (!generatedToken) return;
    await navigator.clipboard.writeText(generatedToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (loadError) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{loadError}</Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 720, mx: "auto" }}>
      <Typography variant="h5" gutterBottom fontWeight={700}>
        {t("profile.title")}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        {t("profile.subtitle")}
      </Typography>

      {/* ── Account Info Card ─────────────────────────────────────── */}
      <Card variant="outlined" sx={{ mb: 3 }}>
        <CardHeader
          avatar={<PersonIcon />}
          title={t("profile.account_info")}
          titleTypographyProps={{ variant: "subtitle1", fontWeight: 600 }}
        />
        <Divider />
        <CardContent>
          <Stack spacing={1.5}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography variant="body2" color="text.secondary" sx={{ minWidth: 100 }}>
                {t("profile.email")}:
              </Typography>
              <Typography variant="body2" fontWeight={500}>
                {user?.email ?? "—"}
              </Typography>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography variant="body2" color="text.secondary" sx={{ minWidth: 100 }}>
                {t("profile.user_id")}:
              </Typography>
              <Typography variant="body2" fontFamily="monospace" fontSize={12}>
                {user?.id ?? "—"}
              </Typography>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography variant="body2" color="text.secondary" sx={{ minWidth: 100 }}>
                {t("profile.status")}:
              </Typography>
              <Chip
                label={user?.is_active ? t("profile.active") : t("profile.inactive")}
                color={user?.is_active ? "success" : "default"}
                size="small"
              />
              {user?.is_superuser && (
                <Chip label={t("profile.superuser")} color="warning" size="small" />
              )}
            </Box>
          </Stack>
        </CardContent>
      </Card>

      {/* ── Password Change Card ──────────────────────────────────── */}
      <Card variant="outlined" sx={{ mb: 3 }}>
        <CardHeader
          avatar={<LockResetIcon />}
          title={t("profile.change_password")}
          titleTypographyProps={{ variant: "subtitle1", fontWeight: 600 }}
        />
        <Divider />
        <CardContent>
          <Box component="form" onSubmit={handleChangePassword}>
            <Stack spacing={2}>
              {pwError && <Alert severity="error" onClose={() => setPwError(null)}>{pwError}</Alert>}
              {pwSuccess && <Alert severity="success" onClose={() => setPwSuccess(null)}>{pwSuccess}</Alert>}

              <TextField
                label={t("profile.current_password")}
                type={showCurrentPw ? "text" : "password"}
                size="small"
                fullWidth
                required
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                slotProps={{
                  input: {
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton size="small" onClick={() => setShowCurrentPw(!showCurrentPw)}>
                          {showCurrentPw ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  },
                }}
              />
              <TextField
                label={t("profile.new_password")}
                type={showNewPw ? "text" : "password"}
                size="small"
                fullWidth
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                slotProps={{
                  input: {
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton size="small" onClick={() => setShowNewPw(!showNewPw)}>
                          {showNewPw ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  },
                }}
              />
              <TextField
                label={t("profile.confirm_password")}
                type="password"
                size="small"
                fullWidth
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
              <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
                <Button
                  type="submit"
                  variant="contained"
                  size="small"
                  disabled={pwLoading || !currentPassword || !newPassword || !confirmPassword}
                >
                  {t("profile.change_password")}
                </Button>
              </Box>
            </Stack>
          </Box>
        </CardContent>
      </Card>

      {/* ── Appearance: the accent and mode, kept in this browser ──── */}
      <Card variant="outlined" sx={{ mb: 3 }}>
        <CardHeader
          avatar={<PaletteIcon />}
          title={t("settings.appearance.title")}
          titleTypographyProps={{ variant: "subtitle1", fontWeight: 600 }}
        />
        <Divider />
        <CardContent>
          <AppearanceSettings />
        </CardContent>
      </Card>

      {/* ── API Token Generation Card ─────────────────────────────── */}
      <Card variant="outlined">
        <CardHeader
          avatar={<KeyIcon />}
          title={t("profile.api_token")}
          subheader={t("profile.api_token_desc")}
          titleTypographyProps={{ variant: "subtitle1", fontWeight: 600 }}
        />
        <Divider />
        <CardContent>
          <Stack spacing={2}>
            {tokenError && <Alert severity="error" onClose={() => setTokenError(null)}>{tokenError}</Alert>}

            <TextField
              label={t("profile.token_name")}
              size="small"
              fullWidth
              value={tokenName}
              onChange={(e) => setTokenName(e.target.value)}
              helperText={t("profile.token_name_help")}
              inputProps={{ maxLength: 200 }}
              data-tour-id="profile.token.name"
            />

            <TextField
              label={t("profile.tenant_id")}
              size="small"
              fullWidth
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              helperText={t("profile.tenant_id_help")}
            />

            <FormControl size="small" fullWidth data-tour-id="profile.token.lifetime">
              <InputLabel>{t("profile.token_expiry")}</InputLabel>
              <Select
                value={expiresIn === null ? "none" : String(expiresIn)}
                label={t("profile.token_expiry")}
                onChange={(e) => {
                  const v = e.target.value;
                  setExpiresIn(v === "none" ? null : Number(v));
                }}
              >
                {EXPIRY_OPTIONS.map((opt) => (
                  <MenuItem key={opt.value ?? "none"} value={opt.value === null ? "none" : String(opt.value)}>
                    {t(opt.key)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <Button
              variant="contained"
              size="small"
              startIcon={<KeyIcon />}
              onClick={handleGenerateToken}
              disabled={tokenLoading || !tenantId.trim()}
              sx={{ alignSelf: "flex-start" }}
              data-tour-id="profile.token.create"
            >
              {t("profile.generate_token")}
            </Button>

            {generatedToken && (
              <Box sx={{ mt: 1 }} data-tour-id="profile.token.value">
                <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
                  {t("profile.generated_token_label")}
                </Typography>
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    bgcolor: "action.hover",
                    borderRadius: 1,
                    p: 1,
                  }}
                >
                  <Typography
                    variant="body2"
                    fontFamily="monospace"
                    fontSize={11}
                    sx={{
                      wordBreak: "break-all",
                      flexGrow: 1,
                      userSelect: "all",
                    }}
                  >
                    {generatedToken}
                  </Typography>
                  <Tooltip title={copied ? t("profile.copied") : t("profile.copy_token")}>
                    <IconButton size="small" onClick={handleCopyToken}>
                      <ContentCopyIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
                <Alert severity="warning" sx={{ mt: 1 }}>
                  {t("profile.token_warning")}
                </Alert>
              </Box>
            )}

            <Divider />
            <Typography variant="subtitle2" data-tour-id="profile.token.list">
              {t("profile.your_tokens")}
            </Typography>
            {tokens !== null && tokens.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                {t("profile.no_tokens")}
              </Typography>
            )}
            {(tokens ?? []).map((tok) => {
              const expired = !tok.revoked_at && !!tok.expires_at && new Date(tok.expires_at).getTime() < Date.now();
              const live = !tok.revoked_at && !expired;
              return (
                <Box
                  key={tok.id}
                  sx={{ display: "flex", alignItems: "center", gap: 1.5, opacity: live ? 1 : 0.6 }}
                >
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600} noWrap>
                      {tok.name || t("profile.unnamed_token")}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t("profile.token_created", { date: formatDate(tok.created_at) })}
                      {" · "}
                      {tok.expires_at
                        ? t("profile.token_expires", { date: formatDate(tok.expires_at) })
                        : t("profile.token_never_expires")}
                    </Typography>
                  </Box>
                  {tok.revoked_at && <Chip size="small" label={t("profile.token_revoked")} />}
                  {expired && <Chip size="small" label={t("profile.token_expired")} />}
                  {live && (
                    <Button size="small" color="error" onClick={() => setRevokeTarget(tok)}>
                      {t("profile.revoke_token")}
                    </Button>
                  )}
                </Box>
              );
            })}
          </Stack>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!revokeTarget}
        title={t("profile.revoke_token")}
        message={t("profile.revoke_token_confirm")}
        confirmLabel={t("profile.revoke_token")}
        cancelLabel={t("common.cancel")}
        loading={revoking}
        onConfirm={handleRevoke}
        onClose={() => setRevokeTarget(null)}
      />
    </Box>
  );
}
