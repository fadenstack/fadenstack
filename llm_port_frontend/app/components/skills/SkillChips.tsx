/**
 * Small chips that say what state a skill is in: which version chats get,
 * who sees it, when it comes into a chat.
 */
import { useTranslation } from "react-i18next";
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import LockIcon from "@mui/icons-material/Lock";
import GroupsIcon from "@mui/icons-material/Groups";
import PublicIcon from "@mui/icons-material/Public";

import type {
  SkillActivation,
  SkillSummary,
  SkillVisibility,
} from "~/api/skills";

/** "Live v3", "Live v3 · v4 draft", "Draft v1" or "Archived". */
export function SkillStatusChip({ skill }: { skill: SkillSummary }) {
  const { t } = useTranslation();
  if (skill.status === "archived") {
    return <Chip size="small" label={t("skills.status_archived")} />;
  }
  if (skill.published_version == null) {
    return (
      <Chip
        size="small"
        color="info"
        variant="outlined"
        label={t("skills.status_draft_version", { version: skill.latest_version })}
      />
    );
  }
  const pending = skill.latest_version !== skill.published_version;
  return (
    <Tooltip
      title={
        pending
          ? t("skills.status_pending_hint", {
              latest: skill.latest_version,
              live: skill.published_version,
            })
          : ""
      }
    >
      <Chip
        size="small"
        color={pending ? "warning" : "success"}
        label={
          pending
            ? t("skills.status_live_pending", {
                live: skill.published_version,
                latest: skill.latest_version,
              })
            : t("skills.status_live", { version: skill.published_version })
        }
      />
    </Tooltip>
  );
}

const VISIBILITY_ICON: Record<SkillVisibility, React.ReactElement> = {
  private: <LockIcon fontSize="small" />,
  tenant: <GroupsIcon fontSize="small" />,
  global: <PublicIcon fontSize="small" />,
};

export function SkillVisibilityChip({ value }: { value: SkillVisibility }) {
  const { t } = useTranslation();
  return (
    <Chip
      size="small"
      variant="outlined"
      icon={VISIBILITY_ICON[value]}
      label={t(`skills.visibility_${value}`)}
    />
  );
}

export function SkillActivationChip({ value }: { value: SkillActivation }) {
  const { t } = useTranslation();
  return (
    <Tooltip title={t(`skills.activation_${value}_hint`)}>
      <Chip size="small" variant="outlined" label={t(`skills.activation_${value}`)} />
    </Tooltip>
  );
}
