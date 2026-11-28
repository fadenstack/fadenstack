/**
 * How a Mac environment is wired, in one line: the gateway reaches the Mac,
 * the Mac runs vLLM-Metal, and that serves the model. A Mac serves alone, so
 * there is no topology to draw.
 */
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { Fragment } from "react";
import { useTranslation } from "react-i18next";

export function StandaloneWiring({ host, models }: { host: string; models: string[] }) {
  const { t } = useTranslation();
  const steps = [
    t("clusters.wiring_standalone.gateway"),
    host,
    t("clusters.wiring_standalone.engine"),
    models.length > 0 ? models.join(", ") : t("clusters.wiring_standalone.no_model"),
  ].filter(Boolean);
  return (
    <Card variant="outlined" data-testid="standalone-wiring">
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Stack direction="row" alignItems="center" spacing={1} useFlexGap flexWrap="wrap">
          <Typography variant="subtitle2" sx={{ mr: 0.5 }}>
            {t("clusters.detail.wiring")}
          </Typography>
          {steps.map((label, i) => (
            <Fragment key={i}>
              {i > 0 && <ArrowForwardIcon fontSize="small" color="disabled" aria-hidden />}
              <Chip size="small" variant="outlined" label={label} />
            </Fragment>
          ))}
        </Stack>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.75 }}>
          {t("clusters.wiring_standalone.help")}
        </Typography>
      </CardContent>
    </Card>
  );
}
