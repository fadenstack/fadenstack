/**
 * The trusted certificates as the server reads them: the saved ones, and pasted ones before they are saved, each
 * with its expiry and a warning under a CA the mail service refuses.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { mailApi, type MailCertificate, type MailCertificatesRead } from "~/api/mailSettings";
import { formatDateTime } from "~/lib/dates";

/** How long typing pauses before the pasted text is read. */
export const READ_DELAY_MS = 400;

/** *certificates* under *heading*: subject, issuer, expiry, and a warning under a CA the mail service refuses. */
export function CertificateList({
  heading,
  certificates,
  testId,
}: {
  heading: string;
  certificates: MailCertificate[];
  testId: string;
}) {
  const { t } = useTranslation();
  return (
    <Stack spacing={1} data-testid={testId}>
      <Typography variant="caption" component="h3" color="text.secondary">
        {heading}
      </Typography>
      {certificates.map((cert, index) => (
        <Box
          key={`${cert.subject}-${index}`}
          data-testid="mail-certificate"
          sx={{
            p: 1.5,
            border: 1,
            borderColor: cert.expired ? "error.main" : cert.ca_without_key_usage ? "warning.main" : "divider",
            borderRadius: 1,
          }}
        >
          <Typography variant="body2" dir="ltr" sx={{ overflowWrap: "anywhere", textAlign: "start" }}>
            {cert.subject}
          </Typography>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ overflowWrap: "anywhere" }}>
            {t("mail.certificates.issuer", { issuer: cert.issuer })}
          </Typography>
          <Typography variant="caption" color={cert.expired ? "error" : "text.secondary"} display="block">
            {cert.expired
              ? t("mail.certificates.expired", { date: formatDateTime(cert.not_after) })
              : t("mail.certificates.expires", { date: formatDateTime(cert.not_after) })}
          </Typography>
          {cert.ca_without_key_usage && (
            <Typography
              variant="caption"
              color="warning"
              display="block"
              sx={{ mt: 0.5 }}
              data-testid="mail-certificate-no-key-usage"
            >
              {t("mail.certificates.ca_without_key_usage")}
            </Typography>
          )}
        </Box>
      ))}
    </Stack>
  );
}

/**
 * What the certificates field holds while it differs from the saved text: read by the server once typing pauses
 * (nothing is stored), only the answer for the latest text shown. Nothing for the saved text; for an empty field, a
 * read still on its way or one that failed, that the change is not saved yet.
 */
export function PastedCertificates({ text, saved }: { text: string; saved: string }) {
  const { t } = useTranslation();
  const [read, setRead] = useState<{ text: string; result: MailCertificatesRead } | null>(null);
  // Counts the texts asked about: an answer for an earlier one is dropped.
  const asked = useRef(0);
  const pasted = text !== saved && text.trim() !== "";

  useEffect(() => {
    const ask = ++asked.current;
    if (!pasted) return undefined;
    const timer = setTimeout(() => {
      mailApi.certificates(text).then(
        (result) => {
          if (ask === asked.current) setRead({ text, result });
        },
        () => {
          // Not read now: the save reads them.
        },
      );
    }, READ_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text, pasted]);

  if (text === saved) return null;
  const result = pasted && read?.text === text ? read.result : null;
  if (!result) {
    return (
      <Typography variant="caption" color="text.secondary">
        {t("mail.certificates.unsaved")}
      </Typography>
    );
  }
  if (result.problem === "not_certificate") {
    return (
      <Alert severity="warning" data-testid="mail-certificates-pasted-not-certificate">
        {t("mail.certificates.pasted_not_certificate")}
      </Alert>
    );
  }
  if (!result.certificates?.length) {
    return (
      <Alert severity="warning" data-testid="mail-certificates-pasted-unreadable">
        {t("mail.certificates.pasted_unreadable")}
      </Alert>
    );
  }
  return (
    <CertificateList
      heading={t("mail.certificates.pasted")}
      certificates={result.certificates}
      testId="mail-certificates-pasted"
    />
  );
}
