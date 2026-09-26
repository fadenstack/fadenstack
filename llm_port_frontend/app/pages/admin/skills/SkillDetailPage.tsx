/**
 * One skill, for administrators: the shared editor with assignments.
 */
import { useNavigate, useParams } from "react-router";

import IconButton from "@mui/material/IconButton";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";

import SkillEditor from "~/components/skills/SkillEditor";
import { useCan } from "~/lib/useCan";
import { skillRights } from "~/api/skills";

export default function SkillDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const can = useCan();
  if (!id) return null;
  return (
    <SkillEditor
      skillId={id}
      rights={skillRights((p) => can(p))}
      adminView
      back={
        <IconButton onClick={() => navigate("/admin/skills")}>
          <ArrowBackIcon />
        </IconButton>
      }
      onDeleted={() => navigate("/admin/skills")}
    />
  );
}
