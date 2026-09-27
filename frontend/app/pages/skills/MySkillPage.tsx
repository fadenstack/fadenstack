/**
 * One skill, for the people who own it, edit it or use it.
 */
import { useNavigate, useOutletContext, useParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";

import type { AuthUser } from "~/api/auth";
import { skillRights } from "~/api/skills";
import SkillEditor from "~/components/skills/SkillEditor";
import { useSkillsModule } from "~/lib/useSkillsModule";

export default function MySkillPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { permissions } = useOutletContext<{ user: AuthUser; permissions: Set<string> }>();
  const rights = skillRights((p) => permissions.has("*") || permissions.has(p));
  const skillsModule = useSkillsModule();

  let content: React.ReactNode;
  if (skillsModule.loading) content = <CircularProgress />;
  else if (!skillsModule.enabled) content = <Alert severity="info">{t("skills.module_off")}</Alert>;
  else if (!rights.read || !id) content = <Alert severity="warning">{t("skills.no_access")}</Alert>;
  else {
    content = (
      <SkillEditor
        skillId={id}
        rights={rights}
        back={
          <IconButton onClick={() => navigate("/skills")} aria-label={t("skills.back_to_list")}>
            <ArrowBackIcon />
          </IconButton>
        }
        onDeleted={() => navigate("/skills")}
      />
    );
  }
  return (
    <Box sx={{ maxWidth: 1200, mx: "auto", px: { xs: 2, md: 3 }, py: 3, height: "100vh", overflow: "auto" }}>
      {content}
    </Box>
  );
}
