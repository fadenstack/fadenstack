/**
 * Sign in an app by approving its code (pairing, RFC 8628; fadenstack-dev desktop-agent/23 §4.3).
 *
 * An app on the person's computer -- the desktop agent -- shows a code and opens this page with it in the
 * fragment (`/pair#code=XXXX-XXXX`). The person signs in here however the server lets them (password, a
 * provider, MFA), looks the code up, compares it with the app, checks what asks, and approves or denies. The app
 * then gets their agent tokens; it never sees their credentials.
 *
 * - The code never goes into an address the server sees, a log or an error text (`lib/pairCode.ts`): it is
 *   taken from the fragment, which is dropped from the address bar at once, and kept in this tab's
 *   sessionStorage only for the way through the sign-in page.
 * - Opening the link only fills the field (RFC 8628 §5.4): looking up needs **Continue**, approving **Approve**.
 * - The requests are POSTs with the code in the body (`api/pairing.ts`); the backend counts wrong codes and
 *   answers 429 over its limits.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, RefObject } from "react";
import { useNavigate } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { auth, type AuthUser } from "~/api/auth";
import { pairingApi, PairingRefused, type PairingLookup } from "~/api/pairing";
import AppBrand from "~/components/AppBrand";
import BrandMark from "~/components/BrandMark";
import {
  dropKeptCode,
  formatCode,
  isCompleteCode,
  keepCode,
  keptCode,
  PAIR_PATH,
  takeCodeFromAddress,
} from "~/lib/pairCode";
import { helpUrl, PAIR_HELP_DOC } from "~/lib/pageHelp";
import { applyUserLanguage } from "~/lib/userLanguage";

type Step =
  | { kind: "enter" }
  | { kind: "review"; found: PairingLookup; code: string }
  | { kind: "approved" }
  | { kind: "denied" };

type Problem =
  | { kind: "incomplete" | "unknown_code" | "decided" | "not_available" | "failed" }
  | { kind: "too_many"; seconds: number | null };

/** Where the sign-in page brings the person back to: this page, never with the code in the address. */
const SIGN_IN = `/login?next=${encodeURIComponent(PAIR_PATH)}`;

/** A number of days as people read it: days, or hours below one day. */
function span(t: TFunction, days: number, language: string): string {
  if (days >= 1) {
    const n = Math.round(days * 10) / 10;
    return t("pair.days", { count: n, n: n.toLocaleString(language) });
  }
  const hours = Math.max(1, Math.round(days * 24));
  return t("pair.hours", { count: hours, n: hours.toLocaleString(language) });
}

function problemText(t: TFunction, problem: Problem, language: string): string {
  switch (problem.kind) {
    case "too_many": {
      if (problem.seconds === null) return t("pair.too_many_later");
      const minutes = Math.max(1, Math.ceil(problem.seconds / 60));
      return t("pair.too_many", { count: minutes, n: minutes.toLocaleString(language) });
    }
    case "incomplete":
      return t("pair.code_incomplete");
    case "unknown_code":
      return t("pair.unknown_code");
    case "decided":
      return t("pair.decided");
    case "not_available":
      return t("pair.not_available");
    default:
      return t("pair.failed");
  }
}

export default function PairPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const language = i18n.resolvedLanguage || i18n.language || "en";

  const [user, setUser] = useState<AuthUser | null>(null);
  const [code, setCode] = useState("");
  const [step, setStep] = useState<Step>({ kind: "enter" });
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);

  const fieldRef = useRef<HTMLInputElement>(null);
  const questionRef = useRef<HTMLHeadingElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  /** The console sign-in ended: off to the sign-in page, the code kept in this tab for the way back. */
  const toSignIn = useCallback(
    async (keep: string) => {
      keepCode(keep);
      // Forget the remembered profile, or the sign-in page would take the person straight back here.
      await auth.meFresh().catch(() => undefined);
      navigate(SIGN_IN, { replace: true });
    },
    [navigate],
  );

  useEffect(() => {
    let cancelled = false;
    // First of all: the code leaves the address bar, into this tab's storage for the way through the sign-in.
    const fromLink = takeCodeFromAddress();
    if (fromLink) keepCode(fromLink);
    auth
      .me()
      .then((me) => {
        if (cancelled) return;
        setUser(me);
        // The language the person picked in an agent or on another device.
        void applyUserLanguage();
        // Signed in: the kept code goes into the field and out of the storage.
        const kept = keptCode();
        dropKeptCode();
        if (kept) setCode(kept);
      })
      .catch(() => {
        if (!cancelled) navigate(SIGN_IN, { replace: true });
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  // Keyboard and screen reader users land on what changed.
  useEffect(() => {
    if (step.kind === "review") questionRef.current?.focus();
    if (step.kind === "approved" || step.kind === "denied") resultRef.current?.focus();
  }, [step.kind]);

  useEffect(() => {
    if (problem && step.kind === "enter") fieldRef.current?.focus();
  }, [problem, step.kind]);

  function refused(err: unknown, entered: string) {
    if (!(err instanceof PairingRefused)) {
      setProblem({ kind: "failed" });
      return;
    }
    if (err.status === 401) {
      void toSignIn(entered);
      return;
    }
    if (err.status === 403 && err.code === "agent_not_available") {
      setProblem({ kind: "not_available" });
      setStep((current) =>
        current.kind === "review" ? { ...current, found: { ...current.found, available: false } } : current,
      );
      return;
    }
    const back: Problem | null =
      err.status === 404
        ? { kind: "unknown_code" }
        : err.status === 409
          ? { kind: "decided" }
          : err.status === 429
            ? { kind: "too_many", seconds: err.retryAfter }
            : null;
    if (back) {
      // The code is spent, wrong or waiting: back to the field.
      setStep({ kind: "enter" });
      setProblem(back);
    } else {
      setProblem({ kind: "failed" });
    }
  }

  async function lookUp(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!isCompleteCode(code)) {
      setProblem({ kind: "incomplete" });
      return;
    }
    const entered = formatCode(code);
    setBusy(true);
    setProblem(null);
    try {
      const found = await pairingApi.lookup(entered);
      setStep({ kind: "review", found, code: entered });
      if (!found.available) setProblem({ kind: "not_available" });
    } catch (err: unknown) {
      refused(err, entered);
    } finally {
      setBusy(false);
    }
  }

  async function decide(approve: boolean) {
    if (step.kind !== "review" || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await (approve ? pairingApi.approve(step.code) : pairingApi.deny(step.code));
      setCode("");
      setStep({ kind: approve ? "approved" : "denied" });
    } catch (err: unknown) {
      refused(err, step.code);
    } finally {
      setBusy(false);
    }
  }

  if (!user) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", pt: 12 }}>
        <CircularProgress aria-label={t("common.loading")} />
      </Box>
    );
  }

  const codeProblem = problem?.kind === "incomplete" || problem?.kind === "unknown_code";

  return (
    <Box
      component="main"
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
      <Card sx={{ width: "100%", maxWidth: 520 }}>
        <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
          <Stack spacing={2.5}>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <BrandMark size={40} />
              <AppBrand size={22} />
            </Stack>
            <Typography component="h1" variant="h5" fontWeight={700}>
              {t("pair.title")}
            </Typography>
            <Typography variant="body2" color="text.secondary" data-testid="pair-signed-in-as">
              {t("pair.signed_in_as", { email: user.email })}
            </Typography>

            {problem && (
              <Alert severity={problem.kind === "failed" ? "error" : "warning"} data-testid="pair-problem">
                {problemText(t, problem, language)}
              </Alert>
            )}

            {/* The result, announced where it appears (a live region present from the start). */}
            <Box role="status" aria-live="polite" aria-atomic="true">
              {step.kind === "approved" && (
                <Alert severity="success" role="none" ref={resultRef} tabIndex={-1} data-testid="pair-result">
                  {t("pair.approved")}
                </Alert>
              )}
              {step.kind === "denied" && (
                <Alert severity="info" role="none" ref={resultRef} tabIndex={-1} data-testid="pair-result">
                  {t("pair.denied")}
                </Alert>
              )}
            </Box>

            {step.kind === "enter" && (
              <Stack component="form" spacing={2} onSubmit={lookUp} noValidate>
                <Typography variant="body2">{t("pair.intro")}</Typography>
                <TextField
                  label={t("pair.code_label")}
                  value={code}
                  onChange={(e) => {
                    setCode(formatCode(e.target.value));
                    if (codeProblem) setProblem(null);
                  }}
                  inputRef={fieldRef}
                  error={codeProblem}
                  helperText={t("pair.code_help")}
                  fullWidth
                  slotProps={{
                    htmlInput: {
                      autoComplete: "off",
                      autoCapitalize: "characters",
                      autoCorrect: "off",
                      spellCheck: false,
                      "data-testid": "pair-code",
                      style: { fontFamily: "monospace", fontSize: 22, letterSpacing: "0.12em" },
                    },
                  }}
                />
                <Button type="submit" variant="contained" size="large" disabled={busy}>
                  {t("pair.continue")}
                </Button>
              </Stack>
            )}

            {step.kind === "review" && (
              <Review
                step={step}
                busy={busy}
                language={language}
                questionRef={questionRef}
                onDecide={(approve) => void decide(approve)}
              />
            )}
          </Stack>
        </CardContent>
      </Card>
      <Typography component="footer" variant="caption" color="text.secondary">
        <Link
          href={helpUrl(PAIR_HELP_DOC, language)}
          target="_blank"
          rel="noopener"
          color="inherit"
          underline="hover"
          aria-label={`${t("pair.help_link")} ${t("about.new_tab")}`}
        >
          {t("pair.help_link")}
        </Link>
      </Typography>
    </Box>
  );
}

/** What asks to be signed in, the code to compare, and Approve and Deny. */
function Review({
  step,
  busy,
  language,
  questionRef,
  onDecide,
}: {
  step: Extract<Step, { kind: "review" }>;
  busy: boolean;
  language: string;
  questionRef: RefObject<HTMLHeadingElement | null>;
  onDecide: (approve: boolean) => void;
}) {
  const { t } = useTranslation();
  const { found, code } = step;
  const agent = found.agent.name ?? found.agent.slug;
  const app = [found.client.name, found.client.version].filter(Boolean).join(" ");
  const device = found.client.os ? `${found.device_label} (${found.client.os})` : found.device_label;
  const time = new Date(found.created_at).toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit" });

  const facts: [string, string][] = [
    [t("pair.fact_app"), found.client.name ? t("pair.app_value", { app }) : t("pair.app_unnamed")],
    [t("pair.fact_device"), device],
    [
      t("pair.fact_requested"),
      found.requester_ip
        ? t("pair.requested_from", { address: found.requester_ip, time })
        : t("pair.requested_from_unknown", { time }),
    ],
    [
      t("pair.fact_lasts"),
      t("pair.lasts_value", {
        max: span(t, found.sign_in_lasts.max_days, language),
        idle: span(t, found.sign_in_lasts.idle_days, language),
      }),
    ],
  ];

  return (
    <Stack spacing={2} data-testid="pair-review">
      <Typography
        component="h2"
        variant="h6"
        fontWeight={700}
        ref={questionRef}
        tabIndex={-1}
        sx={{ outline: "none", overflowWrap: "anywhere" }}
      >
        {t("pair.question", { agent, device: found.device_label })}
      </Typography>

      <Typography variant="body1" data-testid="pair-compare">
        <Trans
          i18nKey="pair.compare"
          values={{ code }}
          components={{
            code: (
              <Box
                component="strong"
                sx={{ fontFamily: "monospace", fontSize: "1.25em", letterSpacing: "0.1em", whiteSpace: "nowrap" }}
              />
            ),
          }}
        />
      </Typography>

      <Box
        component="dl"
        sx={{
          m: 0,
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "max-content 1fr" },
          columnGap: 2,
          rowGap: { xs: 0.25, sm: 1 },
          "& dt": { color: "text.secondary", typography: "body2" },
          "& dd": { m: 0, mb: { xs: 1, sm: 0 }, typography: "body2", overflowWrap: "anywhere" },
        }}
      >
        {facts.map(([term, value]) => (
          <Box key={term} sx={{ display: "contents" }}>
            <dt>{term}</dt>
            <dd>{value}</dd>
          </Box>
        ))}
      </Box>

      {!found.same_network && (
        <Alert severity="warning" variant="outlined" role="note" data-testid="pair-other-network">
          {t("pair.other_network")}
        </Alert>
      )}

      <Alert severity="warning" role="note">
        {t("pair.warning")}
      </Alert>

      <Stack direction={{ xs: "column-reverse", sm: "row" }} spacing={1.5} justifyContent="flex-end">
        <Button variant="outlined" size="large" disabled={busy} onClick={() => onDecide(false)}>
          {t("pair.deny")}
        </Button>
        <Button variant="contained" size="large" disabled={busy || !found.available} onClick={() => onDecide(true)}>
          {t("pair.approve")}
        </Button>
      </Stack>
    </Stack>
  );
}
