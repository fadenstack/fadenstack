/**
 * A picture model's settings: the steps, guidance and size it uses when a request does not say.
 *
 * A picture model runs in the runtime's image app (diffusers), not vLLM: these are all it takes
 * (the backend's ``IMAGE_SETTINGS``). Shared by the host dialog and a deployment's page.
 */
import { useTranslation } from "react-i18next";

import type { EngineConfig } from "~/lib/engine";

import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

const SIZES = ["1024x1024", "1216x832", "832x1216", "1536x1024", "1024x1536"];

/** "Steps 9", "Guidance 0", "Default size 1216 × 832": what a picture deployment is set to. */
export function pictureSettingLabels(config: EngineConfig, t: (key: string) => string): string[] {
  const out: string[] = [];
  if (config.num_inference_steps !== undefined) out.push(`${t("hosting.pictures_steps")} ${config.num_inference_steps}`);
  if (config.guidance_scale !== undefined) out.push(`${t("hosting.pictures_guidance")} ${config.guidance_scale}`);
  if (config.size !== undefined) out.push(`${t("hosting.pictures_size")} ${String(config.size).replace("x", " × ")}`);
  return out;
}

export function PictureSettings({
  value,
  suggested,
  onChange,
  disabled = false,
}: {
  value: EngineConfig;
  suggested: EngineConfig;
  onChange: (v: EngineConfig) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const set = (key: string, raw: string, parse: (s: string) => number) => {
    const next = { ...value };
    if (raw.trim() === "" || !Number.isFinite(parse(raw))) delete next[key];
    else next[key] = parse(raw);
    onChange(next);
  };
  const hint = (key: string) =>
    suggested[key] !== undefined && suggested[key] === value[key] ? t("hosting.pictures_suggested") : undefined;
  return (
    <Stack spacing={2}>
      <Typography variant="subtitle2">{t("hosting.pictures_title")}</Typography>
      <Typography variant="body2" color="text.secondary">
        {t("hosting.pictures_intro")}
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <TextField
          label={t("hosting.pictures_steps")}
          type="number"
          size="small"
          disabled={disabled}
          value={value.num_inference_steps ?? ""}
          placeholder={t("hosting.pictures_model_default")}
          helperText={hint("num_inference_steps") ?? t("hosting.pictures_steps_help")}
          onChange={(e) => set("num_inference_steps", e.target.value, (s) => Math.round(Number(s)))}
          slotProps={{ htmlInput: { min: 1, max: 100, "data-testid": "host-picture-steps" }, inputLabel: { shrink: true } }}
          sx={{ flex: 1 }}
        />
        <TextField
          label={t("hosting.pictures_guidance")}
          type="number"
          size="small"
          disabled={disabled}
          value={value.guidance_scale ?? ""}
          placeholder={t("hosting.pictures_model_default")}
          helperText={hint("guidance_scale") ?? t("hosting.pictures_guidance_help")}
          onChange={(e) => set("guidance_scale", e.target.value, Number)}
          slotProps={{ htmlInput: { min: 0, max: 30, step: 0.5, "data-testid": "host-picture-guidance" }, inputLabel: { shrink: true } }}
          sx={{ flex: 1 }}
        />
        <TextField
          select
          label={t("hosting.pictures_size")}
          size="small"
          disabled={disabled}
          value={String(value.size ?? "")}
          onChange={(e) => {
            const next = { ...value };
            if (e.target.value) next.size = e.target.value;
            else delete next.size;
            onChange(next);
          }}
          slotProps={{
            htmlInput: { "data-testid": "host-picture-size" },
            // The model's own size is a choice too: shown, not a blank box.
            select: { displayEmpty: true },
            inputLabel: { shrink: true },
          }}
          sx={{ flex: 1 }}
        >
          <MenuItem value="">{t("hosting.pictures_model_default")}</MenuItem>
          {SIZES.map((s) => (
            <MenuItem key={s} value={s}>
              {s.replace("x", " × ")}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
    </Stack>
  );
}
