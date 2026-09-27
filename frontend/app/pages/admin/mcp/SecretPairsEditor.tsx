/**
 * Headers or environment variables for an MCP server: named, write-only.
 *
 * They carry bearer tokens and API keys, so a value is never read back. What
 * is already set shows by name; it can be replaced or removed without the
 * others being entered again. The service merges what is sent: a value sets
 * a key, null removes it, a key left out stays.
 */
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";

export interface SecretPair {
  name: string;
  value: string;
}

export interface SecretEdits {
  /** Keys to set, new or replacing a stored one. */
  pairs: SecretPair[];
  /** Stored keys to remove. */
  removed: string[];
}

export const NO_EDITS: SecretEdits = { pairs: [], removed: [] };

/** The update to send: a value sets a key, null removes it. */
export function toSecretPatch(edits: SecretEdits): Record<string, string | null> {
  const patch: Record<string, string | null> = {};
  for (const name of edits.removed) patch[name] = null;
  for (const { name, value } of edits.pairs) {
    const key = name.trim();
    if (key && value) patch[key] = value;
  }
  return patch;
}

/** For a new server: only the keys given a value. */
export function toSecretMap(edits: SecretEdits): Record<string, string> {
  const map: Record<string, string> = {};
  for (const [key, value] of Object.entries(toSecretPatch(edits))) {
    if (value !== null) map[key] = value;
  }
  return map;
}

interface Props {
  label: string;
  help: string;
  /** Keys already stored on the server. */
  existing?: string[];
  edits: SecretEdits;
  onChange: (edits: SecretEdits) => void;
  namePlaceholder: string;
  valuePlaceholder: string;
  addLabel: string;
}

export function SecretPairsEditor({
  label,
  help,
  existing = [],
  edits,
  onChange,
  namePlaceholder,
  valuePlaceholder,
  addLabel,
}: Props) {
  const { t } = useTranslation();
  const replacing = new Set(edits.pairs.map((pair) => pair.name.trim()));

  function setPair(index: number, pair: SecretPair) {
    onChange({ ...edits, pairs: edits.pairs.map((p, i) => (i === index ? pair : p)) });
  }

  function toggleRemoved(name: string) {
    const removed = edits.removed.includes(name)
      ? edits.removed.filter((n) => n !== name)
      : [...edits.removed, name];
    onChange({ pairs: edits.pairs.filter((p) => p.name.trim() !== name), removed });
  }

  return (
    <Box>
      <Typography variant="subtitle2">{label}</Typography>
      <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>
        {help}
      </Typography>

      {existing.length > 0 && (
        <Stack spacing={0.5} sx={{ mb: 1 }}>
          {existing.map((name) => {
            const removed = edits.removed.includes(name);
            return (
              <Stack key={name} direction="row" spacing={1} alignItems="center">
                <Typography
                  fontFamily="monospace"
                  fontSize="0.85rem"
                  sx={{ flex: 1, textDecoration: removed ? "line-through" : "none" }}
                >
                  {name}
                </Typography>
                <Chip
                  size="small"
                  label={
                    removed
                      ? t("mcp.secret_will_be_removed", "will be removed")
                      : replacing.has(name)
                        ? t("mcp.secret_replaced", "replaced below")
                        : t("mcp.secret_set", "set")
                  }
                  color={removed ? "warning" : "default"}
                  variant="outlined"
                />
                {!removed && !replacing.has(name) && (
                  <Button
                    size="small"
                    onClick={() => onChange({ ...edits, pairs: [...edits.pairs, { name, value: "" }] })}
                  >
                    {t("mcp.secret_replace", "Replace")}
                  </Button>
                )}
                <Button size="small" color={removed ? "primary" : "error"} onClick={() => toggleRemoved(name)}>
                  {removed ? t("common.undo", "Undo") : t("common.remove", "Remove")}
                </Button>
              </Stack>
            );
          })}
        </Stack>
      )}

      <Stack spacing={1}>
        {edits.pairs.map((pair, index) => (
          <Stack key={index} direction="row" spacing={1} alignItems="center">
            <TextField
              size="small"
              label={t("mcp.secret_name", "Name")}
              placeholder={namePlaceholder}
              value={pair.name}
              onChange={(e) => setPair(index, { ...pair, name: e.target.value })}
              sx={{ flex: 1 }}
              inputProps={{ style: { fontFamily: "monospace" } }}
            />
            <TextField
              size="small"
              type="password"
              autoComplete="new-password"
              label={t("mcp.secret_value", "Value")}
              placeholder={valuePlaceholder}
              value={pair.value}
              onChange={(e) => setPair(index, { ...pair, value: e.target.value })}
              sx={{ flex: 2 }}
            />
            <IconButton
              size="small"
              aria-label={t("common.remove", "Remove")}
              onClick={() => onChange({ ...edits, pairs: edits.pairs.filter((_, i) => i !== index) })}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Stack>
        ))}
      </Stack>
      <Button
        size="small"
        startIcon={<AddIcon />}
        sx={{ mt: 1 }}
        onClick={() => onChange({ ...edits, pairs: [...edits.pairs, { name: "", value: "" }] })}
      >
        {addLabel}
      </Button>
    </Box>
  );
}
