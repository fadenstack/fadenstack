/**
 * My Profile → Signed-in apps: the apps and devices (desktop agent, Office add-ins, browser extension) the
 * person is signed in on, each with a "Sign out" that ends that device's sign-in (OWASP ASVS 10.4.9, 10.7.3).
 * The list names the agent by id only; names come from the agents the person may use.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CardHeader from "@mui/material/CardHeader";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import DevicesIcon from "@mui/icons-material/Devices";

import { agentsApi, mySignIns, revokeSignIn, type AgentSignIn } from "~/api/agents";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { fmtAgo } from "~/components/dashboard/format";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

interface Loaded {
  signIns: AgentSignIn[];
  names: Record<string, string>;
}

async function load(): Promise<Loaded> {
  const [signIns, agents] = await Promise.all([mySignIns(), agentsApi.list().catch(() => [])]);
  return { signIns, names: Object.fromEntries(agents.map((a) => [a.id, a.name])) };
}

export function AppSignInsCard() {
  const { t, i18n } = useTranslation();
  const { data, loading, error, refresh } = useAsyncData(load, [], { initialValue: { signIns: [], names: {} } });
  const action = useAction();
  const [target, setTarget] = useState<AgentSignIn | null>(null);

  const appName = (s: AgentSignIn) => data.names[s.agent_id] ?? s.agent_id.slice(0, 8);
  const device = (s: AgentSignIn) => s.device_label || t("app_sign_ins.unnamed_device");
  const lang = i18n.language || undefined;

  async function signOut() {
    if (!target) return;
    const device_id = target.device_id;
    setTarget(null);
    await action.run(() => revokeSignIn(device_id), () => refresh());
  }

  return (
    <Card variant="outlined" sx={{ mb: 3 }} data-testid="app-sign-ins-card">
      <CardHeader
        avatar={<DevicesIcon />}
        title={t("app_sign_ins.title")}
        subheader={t("app_sign_ins.intro")}
        titleTypographyProps={{ variant: "subtitle1", fontWeight: 600 }}
      />
      <Divider />
      <CardContent>
        {error && <Alert severity="error">{t("app_sign_ins.load_failed")}</Alert>}
        {action.error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => action.setError(null)}>
            {t("app_sign_ins.sign_out_failed")}
          </Alert>
        )}
        {loading && data.signIns.length === 0 && !error && <CircularProgress size={20} aria-label="loading" />}
        {!loading && !error && data.signIns.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            {t("app_sign_ins.none")}
          </Typography>
        )}
        {data.signIns.length > 0 && (
          <Table size="small" data-testid="app-sign-ins">
            <TableHead>
              <TableRow>
                <TableCell>{t("app_sign_ins.col_app")}</TableCell>
                <TableCell>{t("app_sign_ins.col_device")}</TableCell>
                <TableCell>{t("app_sign_ins.col_signed_in")}</TableCell>
                <TableCell>{t("app_sign_ins.col_last_used")}</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {data.signIns.map((s) => (
                <TableRow key={s.device_id}>
                  <TableCell>{appName(s)}</TableCell>
                  <TableCell>{device(s)}</TableCell>
                  <TableCell>{new Date(s.created_at).toLocaleDateString(lang)}</TableCell>
                  <TableCell>{s.last_used_at ? fmtAgo(s.last_used_at, lang) : t("app_sign_ins.never_used")}</TableCell>
                  <TableCell align="right">
                    <Button size="small" color="error" disabled={action.busy} onClick={() => setTarget(s)}>
                      {t("app_sign_ins.sign_out")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <ConfirmDialog
        open={!!target}
        title={t("app_sign_ins.confirm_title", { agent: target ? appName(target) : "" })}
        message={t("app_sign_ins.confirm", { agent: target ? appName(target) : "", device: target ? device(target) : "" })}
        confirmLabel={t("app_sign_ins.sign_out")}
        cancelLabel={t("common.cancel")}
        loading={action.busy}
        onConfirm={() => void signOut()}
        onClose={() => setTarget(null)}
      />
    </Card>
  );
}
