/**
 * My Profile → Linked sign-ins: the providers one signs in with, linking another while signed in, unlinking
 * one (never the last way in). A provider people are sent to comes back here with `?linked=<key>` or
 * `?link_refused=<code>`; a directory is linked with its account name and password in a dialog.
 * Shown only while a sign-in module offers providers or the account has a linked one.
 */
import type { TFunction } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearchParams } from "react-router";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CardHeader from "@mui/material/CardHeader";
import Divider from "@mui/material/Divider";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import LinkIcon from "@mui/icons-material/Link";

import { adminAuthProviders, type AuthProviderPublic } from "~/api/admin";
import { signInsApi, type LinkedSignIn, type SignIns } from "~/api/signIns";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";
import { refusalText } from "~/lib/signInRefusals";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

import { LinkedSignInsList, providerLabel } from "./LinkedSignInsList";

interface Loaded {
  signIns: SignIns | null;
  providers: AuthProviderPublic[];
}

async function load(): Promise<Loaded> {
  const [signIns, providers] = await Promise.all([
    signInsApi.mine(),
    adminAuthProviders.listPublic().catch(() => [] as AuthProviderPublic[]),
  ]);
  return { signIns, providers };
}

/** A refusal's words: the link's own, else the sign-in page's. */
function refused(t: TFunction, code: string): string {
  return code === "already_linked" || code === "last_way" ? t(`sign_ins.refused.${code}`) : refusalText(t, code);
}

export function LinkedSignInsCard() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const back = useLocation().pathname.startsWith("/chat") ? "/chat" : "/admin/profile";
  const { data, error, refresh } = useAsyncData(load, [], { initialValue: { signIns: null, providers: [] } });
  const action = useAction();
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const [directory, setDirectory] = useState<AuthProviderPublic | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [unlinking, setUnlinking] = useState<LinkedSignIn | null>(null);

  const { signIns, providers } = data;
  const linkedKey = params.get("linked");
  const linkRefused = params.get("link_refused");
  if (!signIns || (providers.length === 0 && signIns.links.length === 0)) {
    return error ? <Alert severity="error">{error}</Alert> : null;
  }
  const linkable = providers.filter((p) => !signIns.links.some((link) => link.provider === p.key));
  // Only a provider this account is linked to: the address names it, and must put no words of its own here.
  const justLinked = signIns.links.find((link) => link.provider === linkedKey);

  function dismiss() {
    const next = new URLSearchParams(params);
    next.delete("linked");
    next.delete("link_refused");
    setParams(next, { replace: true });
  }

  async function link(provider: AuthProviderPublic) {
    setMenu(null);
    if (provider.kind === "password") {
      setUsername("");
      setPassword("");
      setDirectory(provider);
      return;
    }
    await action.run(() => signInsApi.startLink(provider.key, back), ({ redirect_url }) => {
      window.location.assign(redirect_url);
    });
  }

  async function linkDirectory() {
    if (!directory) return;
    const done = await action.run(
      () => signInsApi.linkDirectory(directory.key, username.trim(), password),
      () => refresh(),
    );
    if (done) setDirectory(null);
  }

  async function unlink() {
    if (!unlinking) return;
    await action.run(() => signInsApi.unlinkMine(unlinking.id), () => refresh());
    setUnlinking(null);
  }

  return (
    <Card variant="outlined" sx={{ mb: 3 }} data-testid="linked-sign-ins-card">
      <CardHeader
        avatar={<LinkIcon />}
        title={t("sign_ins.title")}
        subheader={t("sign_ins.intro")}
        titleTypographyProps={{ variant: "subtitle1", fontWeight: 600 }}
      />
      <Divider />
      <CardContent>
        <Stack spacing={2}>
          {justLinked && (
            <Alert severity="success" onClose={dismiss}>
              {t("sign_ins.linked", { name: providerLabel(justLinked) })}
            </Alert>
          )}
          {linkRefused && (
            <Alert severity="error" onClose={dismiss}>
              {refused(t, linkRefused)}
            </Alert>
          )}
          {action.error && !directory && <Alert severity="error">{refused(t, action.error)}</Alert>}
          <LinkedSignInsList signIns={signIns} own busy={action.busy} onUnlink={setUnlinking} />
          {linkable.length > 0 && (
            <>
              <Button
                variant="outlined"
                size="small"
                startIcon={<LinkIcon />}
                sx={{ alignSelf: "flex-start" }}
                disabled={action.busy}
                onClick={(e) => setMenu(e.currentTarget)}
              >
                {t("sign_ins.link")}
              </Button>
              <Menu anchorEl={menu} open={!!menu} onClose={() => setMenu(null)}>
                {linkable.map((provider) => (
                  <MenuItem key={provider.key} onClick={() => void link(provider)}>
                    {provider.name}
                  </MenuItem>
                ))}
              </Menu>
            </>
          )}
        </Stack>
      </CardContent>

      <FormDialog
        open={!!directory}
        title={t("sign_ins.link_title", { name: directory?.name ?? "" })}
        loading={action.busy}
        submitLabel={t("sign_ins.link_submit")}
        cancelLabel={t("common.cancel")}
        submitDisabled={!username.trim() || !password}
        onSubmit={() => void linkDirectory()}
        onClose={() => setDirectory(null)}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            size="small"
            label={t("sign_ins.account_name")}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
          <TextField
            size="small"
            type="password"
            label={t("sign_ins.password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
          {action.error && <Alert severity="error">{refused(t, action.error)}</Alert>}
        </Stack>
      </FormDialog>

      <ConfirmDialog
        open={!!unlinking}
        title={t("sign_ins.unlink_title", { name: unlinking ? providerLabel(unlinking) : "" })}
        message={t("sign_ins.unlink_confirm_own", { name: unlinking ? providerLabel(unlinking) : "" })}
        confirmLabel={t("sign_ins.unlink")}
        cancelLabel={t("common.cancel")}
        loading={action.busy}
        onConfirm={() => void unlink()}
        onClose={() => setUnlinking(null)}
      />
    </Card>
  );
}
