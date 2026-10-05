/**
 * GuideRunner — runs one guide (the console tour or a how-to) over the live console.
 *
 * The runner owns the flow: it goes to the step's page, waits for the step's
 * target, moves on when the person has done what an action step asks, and
 * stops when the dialog a step explains is closed. Joyride only draws, one step
 * at a time, above dialogs and drawers (MUI's are at 1200-1300).
 *
 * A step whose target never appears is skipped: a page without clusters has no
 * cluster picker, and the guide should go on rather than hang.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Joyride, type Step, type TooltipRenderProps } from "react-joyride";
import { useLocation, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useTheme } from "@mui/material/styles";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CloseIcon from "@mui/icons-material/Close";
import TouchAppOutlinedIcon from "@mui/icons-material/TouchAppOutlined";

import { useServices } from "~/lib/ServicesContext";
import {
  eligibleSteps,
  getGuide,
  markGuideCompleted,
  stepBodyKey,
  stepTitleKey,
  type GuideStepDef,
} from "~/lib/guides";
import { announceGuidesChanged } from "~/lib/guidesContext";
import { RichText } from "~/components/guides/RichText";

/** Above MUI dialogs (1300) and snackbars (1400), below MUI tooltips (1500). */
export const GUIDE_Z_INDEX = 1450;
const DEFAULT_WAIT_MS = 2500;
const TICK_MS = 150;
/** How long a step's target may be missing before the guide stops. */
const GONE_MS = 1500;

/** The first match of *selector* that is actually on screen. */
export function findVisible(selector: string): HTMLElement | null {
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") continue;
    return el;
  }
  return null;
}

interface GuideUi {
  index: number;
  total: number;
  action: boolean;
  isLast: boolean;
  canGoBack: boolean;
  next: () => void;
  back: () => void;
  close: () => void;
}

const GuideUiContext = createContext<GuideUi | null>(null);

function GuideTooltip({ step, tooltipProps }: TooltipRenderProps) {
  const ui = useContext(GuideUiContext);
  const theme = useTheme();
  const { t } = useTranslation("tour");
  if (!ui) return null;
  const progress = ((ui.index + 1) / ui.total) * 100;

  return (
    <Box
      {...tooltipProps}
      data-guide-tooltip=""
      sx={{
        bgcolor: "background.paper",
        color: "text.primary",
        borderRadius: 1,
        boxShadow: theme.shadows[8],
        width: 360,
        maxWidth: "calc(100vw - 32px)",
        overflow: "hidden",
      }}
    >
      <LinearProgress variant="determinate" value={progress} sx={{ height: 3 }} />
      <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ px: 2, pt: 1.5 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1, pt: 0.5 }}>
          {step.title as string}
        </Typography>
        <IconButton size="small" aria-label={t("guides.ui.close")} onClick={ui.close} data-guide-close="">
          <CloseIcon fontSize="small" />
        </IconButton>
      </Stack>
      <Typography component="div" variant="body2" color="text.secondary" sx={{ px: 2, pt: 0.5, pb: 1.5 }}>
        <RichText text={step.content as string} />
      </Typography>
      {ui.action && !ui.isLast && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mx: 2, mb: 1.5, px: 1.25, py: 0.75, borderRadius: 1, bgcolor: "action.hover" }}
        >
          <TouchAppOutlinedIcon fontSize="small" color="primary" />
          <Typography variant="caption">{t("guides.ui.your_turn")}</Typography>
        </Stack>
      )}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, pb: 1.5 }}>
        <Typography variant="caption" color="text.disabled" sx={{ flexGrow: 1 }} data-guide-counter="">
          {t("guides.ui.step_counter", { current: ui.index + 1, total: ui.total })}
        </Typography>
        {ui.canGoBack && (
          <Button size="small" variant="text" onClick={ui.back} data-guide-back="">
            {t("guides.ui.back")}
          </Button>
        )}
        {(!ui.action || ui.isLast) && (
          <Button size="small" variant="contained" onClick={ui.next} data-guide-next="">
            {ui.isLast ? t("guides.ui.finish") : t("guides.ui.next")}
          </Button>
        )}
      </Stack>
    </Box>
  );
}

export interface GuideRunnerProps {
  /** The guide to run; null runs nothing. */
  guideId: string | null;
  isSuperuser: boolean;
  /** The guide ended: finished, closed, or stopped because its dialog was closed. */
  onEnd: () => void;
}

export function GuideRunner({ guideId, isSuperuser, onEnd }: GuideRunnerProps) {
  const { t } = useTranslation("tour");
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const { isModuleEnabled } = useServices();

  const guide = guideId ? getGuide(guideId) : undefined;
  const steps = useMemo<GuideStepDef[]>(
    () => (guide ? eligibleSteps(guide, { isModuleEnabled, isSuperuser }) : []),
    [guide, isModuleEnabled, isSuperuser],
  );

  const [index, setIndex] = useState(0);
  const [shown, setShown] = useState(false);
  const [canGoBack, setCanGoBack] = useState(false);
  const goneFor = useRef(0);
  /** The dialog or drawer the step's target is in, if any. */
  const modal = useRef<Element | null>(null);

  const step = steps[index];
  const nextStep = steps[index + 1];
  const isLast = index === steps.length - 1;

  useEffect(() => {
    setIndex(0);
    setShown(false);
  }, [guideId]);

  // The layout re-renders often (polls); a new onEnd must not restart the step.
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;
  const end = useCallback(
    (completed: boolean) => {
      if (completed && guide) {
        void markGuideCompleted(guide.id, guide.version)
          .then(announceGuidesChanged)
          .catch(() => {});
      }
      onEndRef.current();
    },
    [guide],
  );

  /** Back is offered only to a step one can still see: not into a dialog already closed. */
  const backPossible = useCallback(() => {
    const previous = steps[index - 1];
    return Boolean(previous && !previous.action && findVisible(previous.target));
  }, [steps, index]);

  const advance = useCallback(() => {
    if (isLast) end(true);
    else setIndex((i) => i + 1);
  }, [isLast, end]);

  // The step's page.
  useEffect(() => {
    if (!guide || !step) return;
    const route = step.route ?? (index === 0 ? guide.route : undefined);
    if (route && !location.pathname.startsWith(route)) navigate(route);
    // Only when the step changes: following the location would fight the person's own navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guide, step, index]);

  // Wait for the step's target; skip the step if it never comes.
  useEffect(() => {
    if (!step) return;
    setShown(false);
    goneFor.current = 0;
    const deadline = Date.now() + (step.waitMs ?? DEFAULT_WAIT_MS);
    const timer = window.setInterval(() => {
      const el = findVisible(step.target);
      if (el) {
        window.clearInterval(timer);
        modal.current = el.closest(".MuiModal-root");
        setCanGoBack(backPossible());
        setShown(true);
      } else if (Date.now() > deadline) {
        window.clearInterval(timer);
        // A step in a dialog that never opened: the rest of the guide is in it too.
        if (step.dialog && index > 0) end(false);
        else advance();
      }
    }, TICK_MS);
    return () => window.clearInterval(timer);
    // Not on backPossible: it changes with the index, and so does the step.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, advance]);

  // While a step shows: move on when the person has acted, stop when its target goes away.
  useEffect(() => {
    if (!step || !shown) return;
    const timer = window.setInterval(() => {
      if (step.action && nextStep && findVisible(nextStep.target)) {
        window.clearInterval(timer);
        setIndex((i) => i + 1);
        return;
      }
      setCanGoBack(backPossible());
      if (findVisible(step.target)) {
        goneFor.current = 0;
        return;
      }
      goneFor.current += TICK_MS;
      // Doing what an action step asks may hide its target before the next one
      // is there (a wizard's Next that first plans the network): wait for it,
      // as long as the dialog is still open. A closed one was cancelled.
      const dialogOpen = !modal.current || modal.current.isConnected;
      const patience = step.action && nextStep && dialogOpen ? (nextStep.waitMs ?? DEFAULT_WAIT_MS) : GONE_MS;
      if (goneFor.current >= patience) {
        window.clearInterval(timer);
        // The last step's dialog closing is the task being done (Create, Host);
        // any other target going away means the person left the guide.
        end(isLast);
      }
    }, TICK_MS);
    return () => window.clearInterval(timer);
  }, [step, nextStep, shown, isLast, end, backPossible]);

  // The highlight ring: dialog steps have no overlay to show where to look.
  useEffect(() => {
    if (!step || !shown) return;
    const el = findVisible(step.target);
    el?.classList.add("guide-target");
    // Joyride does not scroll for dialog steps (it would scroll the page behind).
    if (step.dialog) el?.scrollIntoView({ block: "nearest", inline: "nearest" });
    return () => el?.classList.remove("guide-target");
  }, [step, shown]);

  const ui = useMemo<GuideUi>(
    () => ({
      index,
      total: steps.length,
      action: Boolean(step?.action),
      isLast,
      canGoBack: index > 0 && canGoBack,
      next: advance,
      back: () => setIndex((i) => Math.max(0, i - 1)),
      close: () => end(false),
    }),
    [index, steps.length, step, isLast, canGoBack, advance, end],
  );

  if (!guide || !step || !shown) return null;

  const joyrideStep: Step = {
    target: step.target,
    title: t(stepTitleKey(guide.id, step.id)),
    content: t(stepBodyKey(guide.id, step.id)),
    placement: step.placement ?? "auto",
    skipBeacon: true,
    hideOverlay: Boolean(step.dialog),
    skipScroll: Boolean(step.dialog),
    targetWaitTimeout: 0,
  };

  return (
    <GuideUiContext.Provider value={ui}>
      <style>{`
        .guide-target {
          outline: 2px solid ${theme.palette.primary.main} !important;
          outline-offset: 3px;
          animation: guide-pulse 1.6s ease-in-out infinite;
        }
        @keyframes guide-pulse {
          0%, 100% { outline-color: ${theme.palette.primary.main}; }
          50% { outline-color: ${theme.palette.primary.light}; }
        }
      `}</style>
      <Joyride
        key={`${guide.id}-${index}`}
        run
        steps={[joyrideStep]}
        tooltipComponent={GuideTooltip}
        options={{
          zIndex: GUIDE_Z_INDEX,
          overlayClickAction: false,
          dismissKeyAction: false,
          disableFocusTrap: true,
          blockTargetInteraction: false,
          spotlightPadding: 6,
          overlayColor: "rgba(0, 0, 0, 0.45)",
          arrowColor: theme.palette.background.paper,
        }}
      />
    </GuideUiContext.Provider>
  );
}
