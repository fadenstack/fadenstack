/**
 * "Help" beside a usage section, to the Help section that explains it, in the
 * reader's language where Help has one (English and German). `doc` is a Help
 * page with an optional section: `administer/usage-privacy#the-settings`.
 */
import { useTranslation } from "react-i18next";

import Link from "@mui/material/Link";

import { helpUrl } from "~/lib/pageHelp";

export function UsageHelpLink({ doc }: { doc: string }) {
  const { t, i18n } = useTranslation();
  return (
    <Link
      href={helpUrl(doc, i18n.language)}
      target="_blank"
      rel="noopener"
      variant="body2"
      underline="hover"
      sx={{ whiteSpace: "nowrap" }}
      data-help-doc={doc}
    >
      {t("about.help")}
    </Link>
  );
}

/** The Help sections the usage pages link to (Help's own anchors, the same in English and German). */
export const USAGE_HELP = {
  ownUsage: "administer/usage#your-own-usage",
  usagePage: "administer/usage#the-usage-page",
  limits: "administer/usage#limits-for-the-whole-instance",
  settings: "administer/usage-privacy#the-settings",
  onePerson: "administer/usage-privacy#looking-at-one-persons-figures",
  documents: "administer/usage-documents",
} as const;
