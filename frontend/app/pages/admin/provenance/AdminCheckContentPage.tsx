/**
 * Check content in the admin console (/admin/check-content, Utilities): the
 * same check as everyone's page beside the chat, for administrators who work
 * here. The provenance guard shows a notice instead while the module is off.
 */
import { useTranslation } from "react-i18next";

import Stack from "@mui/material/Stack";

import { checkContent } from "~/api/provenance";
import { PageHeader } from "~/components/dashboard/kit";
import ContentCheck from "~/components/contentCheck/ContentCheck";
import { useContentCheck } from "~/lib/useContentCheck";

export default function AdminCheckContentPage() {
  const { t } = useTranslation();
  const { maxBytes } = useContentCheck();

  return (
    <Stack spacing={2} sx={{ maxWidth: 900 }}>
      <PageHeader title={t("content_check.title")} subtitle={t("content_check.intro")} />
      <ContentCheck mode="account" submit={checkContent} maxBytes={maxBytes} />
    </Stack>
  );
}
