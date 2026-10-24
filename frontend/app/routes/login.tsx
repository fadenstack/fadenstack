import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link as RouterLink, useNavigate, useSearchParams } from "react-router";
import { auth } from "~/api/auth";
import { adminAuthProviders, type AuthProviderPublic } from "~/api/admin";
import { clearCachedAccess } from "~/lib/adminConstants";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AppBrand from "~/components/AppBrand";
import BrandMark from "~/components/BrandMark";
import Link from "@mui/material/Link";
import { helpUrl } from "~/lib/pageHelp";
import { EXTERNAL_LINK, websiteUrl } from "~/lib/vendorLinks";

export default function LoginPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/admin/dashboard";

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
        if (!cancelled) setSsoProviders(providers.filter((p) => p.kind === "redirect"));
      })
      .catch(() => {
        // SSO not available — ignore
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

    // Check for OAuth error in query params
    const oauthError = params.get("error");
    if (oauthError) {
      setError(t("common.sso_failed", { error: oauthError }));
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
      await auth.login(username, password);
      const user = await auth.me();
      navigate(resolvePostLoginPath(user, next), { replace: true });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.login_failed"));
    } finally {
      setLoading(false);
    }
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
          <Stack spacing={2.5} component="form" onSubmit={handleSubmit}>
            <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 0.5 }}>
              <BrandMark size={48} />
              <AppBrand size={24} />
            </Stack>
            <Typography variant="h5" fontWeight={700}>
              {t("auth.login_title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("auth.login_desc")}
            </Typography>

            {error && <Alert severity="error">{error}</Alert>}

            <TextField
              label={t("auth.email")}
              type="email"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
            <TextField
              label={t("auth.password")}
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            <Button
              component={RouterLink}
              to="/forgot-password"
              size="small"
              sx={{ alignSelf: "flex-start", mt: -1 }}
            >
              {t("auth.forgot_password")}
            </Button>

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
        </CardContent>
      </Card>
      {/* Small and muted, under the card: the vendor's site (tagged, no version: anyone sees this page) and
          this server's own Help. */}
      <Typography component="footer" variant="caption" color="text.secondary" data-testid="signin-footer">
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
        <Link href={helpUrl(undefined, i18n.language)} target="_blank" rel="noopener" color="inherit" underline="hover">
          {t("about.help")}
        </Link>
      </Typography>
    </Box>
  );
}
