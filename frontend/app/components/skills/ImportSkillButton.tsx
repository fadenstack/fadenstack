/**
 * Import a skill from a SKILL.md or a zipped skill folder (the Agent Skills
 * format Claude Code, Codex and Open WebUI use too).
 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import UploadFileIcon from "@mui/icons-material/UploadFile";

import { importSkill, type SkillDetail } from "~/api/skills";

interface Props {
  onImported: (skill: SkillDetail) => void;
  onError: (message: string) => void;
  /** Administrators import for the organization. */
  organization?: boolean;
}

export default function ImportSkillButton({ onImported, onError, organization }: Props) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  async function upload(file: File) {
    setBusy(true);
    try {
      onImported(
        await importSkill(file, {
          owner: organization ? "organization" : "me",
          visibility: "private",
          publish: true,
        }),
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button
        size="small"
        startIcon={<UploadFileIcon />}
        disabled={busy}
        onClick={() => input.current?.click()}
        title={t("skills.import_hint")}
      >
        {busy ? t("skills.importing") : t("skills.import")}
      </Button>
      <input
        ref={input}
        type="file"
        hidden
        accept=".md,.markdown,.zip,text/markdown,application/zip"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void upload(file);
        }}
      />
    </>
  );
}
