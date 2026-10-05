/**
 * ChatTips — a few lines for someone's first chats: files, skills, projects.
 * On the welcome view until dismissed; remembered per user, so it does not
 * come back on another device.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import FolderOutlinedIcon from "@mui/icons-material/FolderOutlined";

import { preferences } from "~/api/preferences";
import { RichText } from "~/components/guides/RichText";

export default function ChatTips({ skills }: { skills: boolean }) {
  const { t } = useTranslation("chat");
  const [show, setShow] = useState(false);

  useEffect(() => {
    let cancelled = false;
    preferences
      .get()
      .then((r) => {
        if (!cancelled && !r.preferences?.chat_tips?.dismissed) setShow(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (!show) return null;

  const tips = [
    { id: "files", icon: AttachFileIcon },
    ...(skills ? [{ id: "skills", icon: AutoAwesomeOutlinedIcon }] : []),
    { id: "projects", icon: FolderOutlinedIcon },
  ];

  return (
    <Box
      data-chat-tips=""
      sx={{ mt: 3, maxWidth: 600, width: "100%", border: 1, borderColor: "divider", borderRadius: 1, p: 2 }}
    >
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
          {t("tips.title")}
        </Typography>
        <Button
          size="small"
          onClick={() => {
            setShow(false);
            void preferences.update({ chat_tips: { dismissed: true } }).catch(() => {});
          }}
        >
          {t("tips.dismiss")}
        </Button>
      </Stack>
      <Stack spacing={1}>
        {tips.map(({ id, icon: Icon }) => (
          <Stack key={id} direction="row" spacing={1.5} alignItems="flex-start">
            <Icon fontSize="small" color="action" sx={{ mt: 0.25 }} />
            <Typography variant="body2" color="text.secondary">
              <RichText text={t(`tips.${id}`)} />
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}
