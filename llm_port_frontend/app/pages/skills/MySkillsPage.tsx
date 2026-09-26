/**
 * The skills a user can see -- their own, the ones shared with them, the
 * organization's -- outside the admin pages. Reached from the chat.
 */
import { useEffect, useState } from "react";
import { useNavigate, useOutletContext } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import SearchIcon from "@mui/icons-material/Search";

import type { AuthUser } from "~/api/auth";
import {
  listSkills,
  skillRights,
  type SkillListView,
  type SkillSummary,
} from "~/api/skills";
import CreateSkillDialog from "~/components/skills/CreateSkillDialog";
import ImportSkillButton from "~/components/skills/ImportSkillButton";
import {
  SkillActivationChip,
  SkillStatusChip,
  SkillVisibilityChip,
} from "~/components/skills/SkillChips";
import { useSkillsModule } from "~/lib/useSkillsModule";

const VIEWS: SkillListView[] = ["mine", "shared", "org", "all"];

export default function MySkillsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { permissions } = useOutletContext<{ user: AuthUser; permissions: Set<string> }>();
  const rights = skillRights((p) => permissions.has("*") || permissions.has(p));
  const skillsModule = useSkillsModule();

  const [view, setView] = useState<SkillListView>("mine");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!skillsModule.enabled || !rights.read) return;
    let cancelled = false;
    setLoading(true);
    listSkills({ view, q: search || undefined })
      .then((list) => {
        if (!cancelled) setItems(list.items);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [view, search, skillsModule.enabled, rights.read]);

  const header = (
    <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2, flexWrap: "wrap" }}>
      <IconButton onClick={() => navigate("/chat")} aria-label={t("skills.back_to_chat")}>
        <ArrowBackIcon />
      </IconButton>
      <Typography variant="h5" sx={{ flexGrow: 1 }}>
        {t("skills.my_title")}
      </Typography>
      {skillsModule.enabled && rights.create && (
        <>
          <ImportSkillButton
            onImported={(s) => navigate(`/skills/${s.id}`)}
            onError={(m) => setError(m)}
          />
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
            {t("skills.create")}
          </Button>
        </>
      )}
    </Stack>
  );

  let body: React.ReactNode;
  if (skillsModule.loading) {
    body = <CircularProgress />;
  } else if (!skillsModule.enabled) {
    body = <Alert severity="info">{t("skills.module_off")}</Alert>;
  } else if (!rights.read) {
    body = <Alert severity="warning">{t("skills.no_access")}</Alert>;
  } else {
    body = (
      <>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("skills.my_intro")}
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }} sx={{ mb: 2 }}>
          <Tabs value={view} onChange={(_, v) => setView(v)} variant="scrollable" sx={{ flexGrow: 1 }}>
            {VIEWS.filter((v) => v !== "all" || rights.manage).map((v) => (
              <Tab key={v} value={v} label={t(`skills.view_${v}`)} />
            ))}
          </Tabs>
          <TextField
            size="small"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("skills.search")}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              },
            }}
          />
        </Stack>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {loading ? (
          <CircularProgress size={28} />
        ) : items.length === 0 ? (
          <Paper variant="outlined" sx={{ p: 3 }}>
            <Typography color="text.secondary">{t(`skills.empty_${view}`)}</Typography>
          </Paper>
        ) : (
          <Stack spacing={1.5}>
            {items.map((s) => (
              <Paper
                key={s.id}
                variant="outlined"
                onClick={() => navigate(`/skills/${s.id}`)}
                sx={{ p: 2, cursor: "pointer", "&:hover": { borderColor: "primary.main" } }}
              >
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  <Typography fontWeight={600}>{s.name}</Typography>
                  <Chip size="small" label={`$${s.slug}`} sx={{ fontFamily: "monospace" }} />
                  <Box sx={{ flexGrow: 1 }} />
                  <SkillStatusChip skill={s} />
                  <SkillVisibilityChip value={s.visibility} />
                  <SkillActivationChip value={s.activation} />
                </Stack>
                {s.description && (
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    {s.description}
                  </Typography>
                )}
              </Paper>
            ))}
          </Stack>
        )}
      </>
    );
  }

  return (
    <Box sx={{ maxWidth: 1100, mx: "auto", px: { xs: 2, md: 3 }, py: 3, height: "100vh", overflow: "auto" }}>
      {header}
      {body}
      <CreateSkillDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(s) => {
          setCreateOpen(false);
          navigate(`/skills/${s.id}`);
        }}
      />
    </Box>
  );
}
