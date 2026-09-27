/**
 * A skill's versions: which one chats get, publishing another (or rolling
 * back to an older one), and what changed between two.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import {
  diffVersions,
  listVersions,
  publishSkill,
  type SkillDetail,
  type SkillVersionSummary,
} from "~/api/skills";

interface Props {
  skill: SkillDetail;
  canPublish: boolean;
  onChanged: (skill: SkillDetail) => void;
}

export default function SkillHistoryPanel({ skill, canPublish, onChanged }: Props) {
  const { t } = useTranslation();
  const [versions, setVersions] = useState<SkillVersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [diff, setDiff] = useState<{ from: number; to: number; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setVersions(await listVersions(skill.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [skill.id]);

  useEffect(() => {
    void load();
  }, [load, skill.latest_version]);

  async function publish(version: number) {
    setBusy(version);
    setError(null);
    try {
      onChanged(await publishSkill(skill.id, version));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function compare(version: number) {
    const older = versions.find((v) => v.version < version);
    if (!older) return;
    setError(null);
    try {
      const text = await diffVersions(skill.id, older.version, version);
      setDiff({ from: older.version, to: version, text });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (loading && versions.length === 0) return <CircularProgress size={24} />;

  return (
    <Stack spacing={2}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary">
        {t("skills.history_hint")}
      </Typography>
      <List dense disablePadding>
        {versions.map((v) => {
          const live = v.version === skill.published_version;
          const older = skill.published_version != null && v.version < skill.published_version;
          return (
            <ListItem
              key={v.version}
              divider
              secondaryAction={
                <Stack direction="row" spacing={1}>
                  {versions.some((o) => o.version < v.version) && (
                    <Button size="small" onClick={() => void compare(v.version)}>
                      {t("skills.history_compare")}
                    </Button>
                  )}
                  {canPublish && !live && (
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={busy !== null}
                      onClick={() => void publish(v.version)}
                    >
                      {busy === v.version
                        ? t("common.saving")
                        : older
                          ? t("skills.history_rollback")
                          : t("skills.publish")}
                    </Button>
                  )}
                </Stack>
              }
              sx={{ pr: 28 }}
            >
              <ListItemText
                primary={
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography fontWeight={600}>v{v.version}</Typography>
                    {live && <Chip size="small" color="success" label={t("skills.history_live")} />}
                    {v.version === skill.latest_version && !live && (
                      <Chip size="small" color="info" variant="outlined" label={t("skills.history_latest")} />
                    )}
                    <Typography variant="body2" noWrap>
                      {v.change_note}
                    </Typography>
                  </Stack>
                }
                secondary={
                  <>
                    {new Date(v.created_at).toLocaleString()}
                    {" · "}
                    <Box component="span" sx={{ fontFamily: "monospace" }} title={v.content_sha256}>
                      {v.content_sha256.slice(0, 12)}
                    </Box>
                  </>
                }
              />
            </ListItem>
          );
        })}
      </List>

      {diff && (
        <Box>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
            <Typography variant="subtitle2">
              {t("skills.history_diff_title", { from: diff.from, to: diff.to })}
            </Typography>
            <Button size="small" onClick={() => setDiff(null)}>
              {t("common.close")}
            </Button>
          </Stack>
          <Paper
            variant="outlined"
            sx={{ p: 1.5, fontFamily: "monospace", fontSize: 13, whiteSpace: "pre-wrap", overflowX: "auto" }}
          >
            {diff.text.trim() === "" ? (
              <Typography variant="body2" color="text.secondary">
                {t("skills.history_no_diff")}
              </Typography>
            ) : (
              diff.text.split("\n").map((line, i) => (
                <Box
                  // eslint-disable-next-line react/no-array-index-key
                  key={i}
                  component="div"
                  sx={{
                    color: line.startsWith("+") && !line.startsWith("+++")
                      ? "success.main"
                      : line.startsWith("-") && !line.startsWith("---")
                        ? "error.main"
                        : line.startsWith("@@")
                          ? "info.main"
                          : "text.primary",
                  }}
                >
                  {line || " "}
                </Box>
              ))
            )}
          </Paper>
        </Box>
      )}
    </Stack>
  );
}
