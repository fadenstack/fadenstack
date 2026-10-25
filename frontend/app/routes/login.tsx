import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link as RouterLink, useNavigate, useSearchParams } from "react-router";
import { auth, LoginRefused } from "~/api/auth";
import { adminAuthProviders, SignInRefused, type AuthProviderPublic } from "~/api/admin";
import { clearCachedAccess } from "~/lib/adminConstants";
import { refusalText } from "~/lib/signInRefusals";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import AppBrand from "~/components/AppBrand";
import BrandMark from "~/components/BrandMark";
import { LinkProof } from "~/components/LinkProof";
import Link from "@mui/material/Link";
import { helpUrl } from "~/lib/pageHelp";
import { EXTERNAL_LINK, websiteUrl } from "~/lib/vendorLinks";

/** The texts for a refused password sign-in, by the backend's code; any other reads as a plain failure. */
const LOGIN_REFUSALS: Record<string, string> = {
  LOGIN_BAD_CREDENTIALS: "auth.login_refused.bad_credentials",
  LOGIN_USER_NOT_VERIFIED: "auth.refused.account_not_verified",
  LOGIN_USE_SIGN_IN_PROVIDER: "auth.login_refused.use_provider",
  TOO_MANY: "auth.login_refused.too_many",
};

export default function LoginPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/admin/dashboard";
  // A sign-in with an outside provider met an existing account: it waits for the proof (LinkProof).
  const [linking, setLinking] = useState(() => params.get("error") === "link_needs_confirmation");
  // A forgotten password is reset by mail only while the server can send mail.
  const [passwordReset, setPasswordReset] = useState(false);
  const [vendorLinks, setVendorLinks] = useState(true);

  function resolvePostLoginPath(
    user: { is_superuser: boolean },
    target: string,
  ) {
    if (!user.is_superuser && target.startsWith("/admin")) {
      return "/chat";
    }
    return target;
  }

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ssoProviders, setSsoProviders] = useState<AuthProviderPublic[]>([]);
  // A directory (LDAP, Active Directory) signs in on this page, with its own account names.
  const [directories, setDirectories] = useState<AuthProviderPublic[]>([]);
  const [accountKind, setAccountKind] = useState<string>("local");
  const directory = directories.find((p) => p.key === accountKind);
  const [isDevMode, setIsDevMode] = useState(false);

  useEffect(() => {
    let cancelled = false;
    clearCachedAccess();
    auth
      .me()
      .then((user) => {
        if (!cancelled) {
          navigate(resolvePostLoginPath(user, next), { replace: true });
        }
      })
      .catch(() => {
        // not logged in
      });

    // Load available SSO providers
    adminAuthProviders
      .listPublic()
      .then((providers) => {
        if (cancelled) return;
        setSsoProviders(providers.filter((p) => p.kind === "redirect"));
        setDirectories(providers.filter((p) => p.kind === "password"));
      })
      .catch(() => {
        // SSO not available — ignore
      });

    auth
      .options()
      .then((options) => {
        if (cancelled) return;
        setPasswordReset(options.password_reset === true);
        setVendorLinks(options.vendor_links !== false);
      })
      .catch(() => {
        // unknown: no reset offered
      });

    // Check backend environment for dev mode features
    fetch("/api/health")
      .then((res) => res.json())
      .then((data: { environment?: string }) => {
        if (!cancelled && data.environment === "dev") {
          setIsDevMode(true);
          setUsername("admin@localhost");
          setPassword("admin");
        }
      })
      .catch(() => {
        // health endpoint unavailable — stay in production mode
      });

    // A sign-in with an outside provider that came back refused
    const refusal = params.get("error");
    if (refusal && refusal !== "link_needs_confirmation") {
      setError(refusalText(t, refusal));
    }

    return () => {
      cancelled = true;
    };
  }, [navigate, next]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      if (directory) await adminAuthProviders.passwordSignIn(directory.key, username, password);
      else await auth.login(username, password);
      // auth.login drops the cached profile itself; a directory sign-in reads it afresh.
      const user = directory ? await auth.meFresh() : await auth.me();
      navigate(resolvePostLoginPath(user, next), { replace: true });
    } catch (err: unknown) {
      if (err instanceof SignInRefused && err.reason === "link_needs_confirmation") setLinking(true);
      else if (err instanceof SignInRefused) setError(refusalText(t, err.reason));
      else if (err instanceof LoginRefused) setError(t(LOGIN_REFUSALS[err.code] ?? "common.login_failed"));
      else setError(t("common.login_failed"));
    } finally {
      setLoading(false);
    }
  }

  /** The waiting sign-in is linked and the person signed in (LinkProof): on, as after any sign-in. */
  async function finishLinking() {
    const user = await auth.meFresh();
    navigate(resolvePostLoginPath(user, next), { replace: true });
  }

  async function handleDevLogin() {
    setLoading(true);
    setError(null);
    try {
      await auth.devLogin();
      const user = await auth.me();
      navigate(resolvePostLoginPath(user, next), { replace: true });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.login_failed"));
    } finally {
      setLoading(false);
    }
  }

  const header = (
    <>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 0.5 }}>
        <BrandMark size={48} />
        <AppBrand size={24} />
      </Stack>
      <Typography variant="h5" fontWeight={700}>
        {t("auth.login_title")}
      </Typography>
    </>
  );

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1.5,
        p: 2,
      }}
    >
      <Card sx={{ width: "100%", maxWidth: 460 }}>
        <CardContent sx={{ p: 4 }}>
          {linking ? (
            <Stack spacing={2.5}>
              {header}
              <LinkProof onSignedIn={finishLinking} onCancel={() => setLinking(false)} />
            </Stack>
          ) : (
            <Stack spacing={2.5} component="form" onSubmit={handleSubmit}>
              {header}
              <Typography variant="body2" color="text.secondary">
                {t("auth.login_desc")}
              </Typography>

              {error && <Alert severity="error">{error}</Alert>}

              {directories.length > 0 && (
                <ToggleButtonGroup
                  exclusive
                  fullWidth
                  size="small"
                  color="primary"
                  value={accountKind}
                  onChange={(_, kind: string | null) => kind && setAccountKind(kind)}
                  aria-label={t("auth.account_kind")}
                >
                  <ToggleButton value="local">{t("auth.local_account")}</ToggleButton>
                  {directories.map((provider) => (
                    <ToggleButton key={provider.key} value={provider.key}>
                      {provider.name}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
              )}

              <TextField
                label={directory ? t("auth.account_name") : t("auth.email")}
                type={directory ? "text" : "email"}
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                helperText={directory ? t("auth.account_name_help", { provider: directory.name }) : undefined}
              />
              <TextField
                label={t("auth.password")}
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
              {/* A directory account's password is the organisation's to reset, not this server's; a local one is
                  reset by mail only while the server can send mail, else by an administrator. */}
              {!directory && passwordReset && (
                <Button
                  component={RouterLink}
                  to="/forgot-password"
                  size="small"
                  sx={{ alignSelf: "flex-start", mt: -1 }}
                >
                  {t("auth.forgot_password")}
                </Button>
              )}
              {!directory && !passwordReset && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: -1 }} data-testid="ask-admin">
                  {t("auth.forgot_password_ask_admin")}
                </Typography>
              )}

              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={loading}
              >
                {t("auth.sign_in")}
              </Button>
              {isDevMode && (
                <Button
                  variant="outlined"
                  disabled={loading}
                  onClick={handleDevLogin}
                >
                  {t("auth.dev_login")}
                </Button>
              )}

              {ssoProviders.length > 0 && (
                <>
                  <Divider>{t("auth.or_sso")}</Divider>
                  {ssoProviders.map((provider) => (
                    <Button
                      key={provider.key}
                      variant="outlined"
                      disabled={loading}
                      onClick={() => {
                        if (typeof window !== "undefined") {
                          window.location.href = adminAuthProviders.startUrl(
                            provider.key,
                          );
                        }
                      }}
                    >
                      {t("auth.sign_in_with", { provider: provider.name })}
                    </Button>
                  ))}
                </>
              )}

              <Button component={RouterLink} to="/" size="small">
                {t("auth.back_home")}
              </Button>
            </Stack>
          )}
        </CardContent>
      </Card>
      {/* Small and muted, under the card: the vendor's site (tagged, no version: anyone sees this page; not
          where the server switched the vendor's links off) and this server's own Help. */}
      <Typography component="footer" variant="caption" color="text.secondary" data-testid="signin-footer">
        {vendorLinks && (
          <>
            <Link
              href={websiteUrl("/", "console-signin")}
              {...EXTERNAL_LINK}
              color="inherit"
              underline="hover"
              aria-label={`Fadenstack ${t("about.new_tab")}`}
            >
              Fadenstack
            </Link>
            {" · "}
          </>
        )}
        <Link href={helpUrl(undefined, i18n.language)} target="_blank" rel="noopener" color="inherit" underline="hover">
          {t("about.help")}
        </Link>
      </Typography>
    </Box>
  );
}
