/**
 * ContentCheck — paste a text or choose a file, and read whether it carries
 * the marks this installation puts on AI-generated content: the verdict in
 * plain words, what each layer found, and a signed result to download
 * (CoP 2.1.2).
 *
 * Signed in, texts, pictures and files are checked; on the public page
 * (`mode="public"`) pictures and files only -- texts are for people with an
 * account. What is checked stays in this page: it is sent, checked and kept
 * nowhere.
 */
import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import CloseIcon from "@mui/icons-material/Close";
import DownloadIcon from "@mui/icons-material/Download";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined";
import SearchOffIcon from "@mui/icons-material/SearchOff";
import UploadFileIcon from "@mui/icons-material/UploadFile";

import { ApiError, errorText } from "~/api/http";
import type {
  ContentCheckBody,
  ContentCheckResult,
  ContentFinding,
  ContentVerdict,
} from "~/api/provenance";
import {
  ACCOUNT_FILE_TYPES,
  KNOWN_ERRORS,
  PUBLIC_FILE_TYPES,
  isTextType,
  mediaTypeOf,
  readAsBase64,
  saveResult,
  scoreLabel,
  sizeLabel,
  textBytes,
} from "~/lib/contentCheck";
import { formatDateTime } from "~/lib/dates";

export interface ContentCheckProps {
  /** account: texts, pictures and files; public: pictures and files. */
  mode: "account" | "public";
  /** Sends one check (signed in or public). */
  submit: (body: ContentCheckBody) => Promise<ContentCheckResult>;
  /** The largest content checked, in bytes. */
  maxBytes: number;
  /** Shown under the form on the public page: whom to ask for a text check. */
  textChecks?: ReactNode;
}

type Source = "text" | "file";

const VERDICT_ICON: Record<ContentVerdict, ReactNode> = {
  "ai-generated": <AutoAwesomeIcon color="primary" />,
  inconclusive: <HelpOutlineIcon color="warning" />,
  "no-mark-found": <SearchOffIcon color="action" />,
};

/** What the person reads for a failed check or download. */
function useErrorText(maxBytes: number): (err: unknown) => string {
  const { t } = useTranslation();
  return (err) => {
    if (err instanceof ApiError) {
      const code = err.code ?? (err.status === 429 ? "rate_limited" : undefined);
      if (code && KNOWN_ERRORS.has(code)) {
        return t(`content_check.errors.${code}`, { size: sizeLabel(maxBytes) });
      }
    }
    return t("content_check.errors.generic", { message: errorText(err) });
  };
}

export default function ContentCheck({ mode, submit, maxBytes, textChecks }: ContentCheckProps) {
  const { t } = useTranslation();
  const errorFor = useErrorText(maxBytes);
  const publicPage = mode === "public";
  const [source, setSource] = useState<Source>(publicPage ? "file" : "text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [signing, setSigning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<ContentCheckResult | null>(null);
  // What was checked last, to ask for its signed result.
  const checked = useRef<ContentCheckBody | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  function clearOutcome() {
    setResult(null);
    setError(null);
    setNotice(null);
    checked.current = null;
  }

  function pickFile(next: File | null) {
    clearOutcome();
    setFile(next);
    if (fileInput.current) fileInput.current.value = "";
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) pickFile(dropped);
  }

  async function bodyToCheck(): Promise<ContentCheckBody | null> {
    if (source === "text") {
      if (!text.trim()) return null;
      if (textBytes(text) > maxBytes) {
        setError(t("content_check.errors.too_large", { size: sizeLabel(maxBytes) }));
        return null;
      }
      return { text };
    }
    if (!file) return null;
    if (file.size > maxBytes) {
      setError(t("content_check.errors.too_large", { size: sizeLabel(maxBytes) }));
      return null;
    }
    const mediaType = mediaTypeOf(file);
    if (publicPage && isTextType(mediaType)) {
      setError(t("content_check.errors.text_not_public"));
      return null;
    }
    try {
      return { file: await readAsBase64(file), media_type: mediaType };
    } catch {
      setError(t("content_check.errors.read_failed"));
      return null;
    }
  }

  async function check() {
    clearOutcome();
    setBusy(true);
    try {
      const body = await bodyToCheck();
      if (!body) return;
      setResult(await submit({ ...body, signed: false }));
      checked.current = body;
    } catch (err) {
      setError(errorFor(err));
    } finally {
      setBusy(false);
    }
  }

  async function downloadSigned() {
    if (!checked.current) return;
    setSigning(true);
    setError(null);
    setNotice(null);
    try {
      const signed = await submit({ ...checked.current, signed: true });
      if (!signed.signature) {
        setNotice(t("content_check.unsigned"));
        return;
      }
      saveResult(signed);
    } catch (err) {
      setError(errorFor(err));
    } finally {
      setSigning(false);
    }
  }

  function startOver() {
    clearOutcome();
    setText("");
    pickFile(null);
  }

  const ready = source === "text" ? text.trim().length > 0 : file !== null;
  const fileTypes = publicPage ? "content_check.public_file_types" : "content_check.file_types";

  return (
    <Stack spacing={2.5}>
      <Paper variant="outlined" sx={{ p: { xs: 2, sm: 2.5 } }}>
        <Stack spacing={2}>
          {!publicPage && (
            <Tabs
              value={source}
              onChange={(_, next: Source) => {
                clearOutcome();
                setSource(next);
              }}
              aria-label={t("content_check.source_label")}
            >
              <Tab value="text" label={t("content_check.tab_text")} />
              <Tab value="file" label={t("content_check.tab_file")} />
            </Tabs>
          )}

          {source === "text" ? (
            <TextField
              label={t("content_check.text_label")}
              placeholder={t("content_check.text_placeholder")}
              value={text}
              onChange={(e) => {
                clearOutcome();
                setText(e.target.value);
              }}
              multiline
              minRows={6}
              maxRows={16}
              fullWidth
            />
          ) : (
            <Box
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              data-testid="content-check-drop"
              sx={{
                border: 1,
                borderStyle: "dashed",
                borderColor: dragging ? "primary.main" : "divider",
                borderRadius: 1,
                bgcolor: dragging ? "accent.tint" : "transparent",
                p: { xs: 2, sm: 3 },
                textAlign: "center",
              }}
            >
              {file ? (
                <Stack direction="row" spacing={1} alignItems="center" justifyContent="center">
                  <InsertDriveFileOutlinedIcon color="action" />
                  <Typography variant="body2" sx={{ wordBreak: "break-all" }} data-testid="content-check-file-name">
                    {t("content_check.selected_file", { name: file.name, size: sizeLabel(file.size) })}
                  </Typography>
                  <Tooltip title={t("content_check.remove_file")}>
                    <IconButton size="small" onClick={() => pickFile(null)} aria-label={t("content_check.remove_file")}>
                      <CloseIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Stack>
              ) : (
                <Stack spacing={1} alignItems="center">
                  <Button component="label" variant="outlined" startIcon={<UploadFileIcon />}>
                    {t("content_check.choose_file")}
                    <input
                      ref={fileInput}
                      hidden
                      type="file"
                      accept={publicPage ? PUBLIC_FILE_TYPES : ACCOUNT_FILE_TYPES}
                      data-testid="content-check-file"
                      onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
                    />
                  </Button>
                  <Typography variant="body2" color="text.secondary">
                    {t("content_check.drop_hint")}
                  </Typography>
                </Stack>
              )}
            </Box>
          )}

          <Typography variant="caption" color="text.secondary">
            {source === "text"
              ? t("content_check.text_limit", { size: sizeLabel(maxBytes) })
              : t(fileTypes, { size: sizeLabel(maxBytes) })}
          </Typography>

          <Stack direction="row" spacing={1} alignItems="center">
            <Button
              variant="contained"
              onClick={() => void check()}
              disabled={!ready || busy}
              data-testid="content-check-submit"
              startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
            >
              {busy ? t("content_check.checking") : t("content_check.check")}
            </Button>
          </Stack>

          {error && (
            <Alert severity="error" onClose={() => setError(null)} data-testid="content-check-error">
              {error}
            </Alert>
          )}
          {textChecks}
        </Stack>
      </Paper>

      {result && (
        <ResultView
          result={result}
          signing={signing}
          notice={notice}
          onDownload={() => void downloadSigned()}
          onStartOver={startOver}
        />
      )}

      <Explanation publicPage={publicPage} />
    </Stack>
  );
}

function ResultView({
  result,
  signing,
  notice,
  onDownload,
  onStartOver,
}: {
  result: ContentCheckResult;
  signing: boolean;
  notice: string | null;
  onDownload: () => void;
  onStartOver: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 2.5 } }} data-testid="content-check-result">
      <Stack spacing={2}>
        <Stack
          direction="row"
          spacing={1.5}
          alignItems="flex-start"
          data-testid="content-check-verdict"
          data-verdict={result.verdict}
        >
          <Box sx={{ pt: 0.25 }}>{VERDICT_ICON[result.verdict]}</Box>
          <Box>
            <Typography variant="h6">{t(`content_check.verdict.${result.verdict}.title`)}</Typography>
            <Typography variant="body2" color="text.secondary">
              {t(`content_check.verdict.${result.verdict}.body`)}
            </Typography>
          </Box>
        </Stack>

        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            {t("content_check.findings")}
          </Typography>
          {result.findings.length === 0 ? (
            <Typography variant="body2" color="text.secondary" data-testid="content-check-no-findings">
              {t("content_check.no_findings")}
            </Typography>
          ) : (
            <Stack spacing={1}>
              {result.findings.map((finding, index) => (
                <FindingView key={`${finding.method}-${index}`} finding={finding} />
              ))}
            </Stack>
          )}
        </Box>

        <Box>
          <Typography variant="caption" color="text.secondary" component="p">
            {t("content_check.checked_by", {
              time: formatDateTime(result.checked_at),
              detector: result.detector,
            })}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            component="p"
            sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
            data-testid="content-check-hash"
          >
            {t("content_check.content_hash", { hash: result.content_hash })}
          </Typography>
        </Box>

        {notice && (
          <Alert severity="info" data-testid="content-check-notice">
            {notice}
          </Alert>
        )}

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button
            variant="outlined"
            startIcon={signing ? <CircularProgress size={16} color="inherit" /> : <DownloadIcon />}
            onClick={onDownload}
            disabled={signing}
            data-testid="content-check-download"
          >
            {signing ? t("content_check.signing") : t("content_check.download_signed")}
          </Button>
          <Button onClick={onStartOver}>{t("content_check.start_over")}</Button>
        </Stack>
        <Typography variant="caption" color="text.secondary">
          {t("content_check.download_hint")}
        </Typography>
      </Stack>
    </Paper>
  );
}

function FindingView({ finding }: { finding: ContentFinding }) {
  const { t } = useTranslation();
  return (
    <Paper variant="outlined" sx={{ p: 1.5 }} data-testid="content-check-finding" data-method={finding.method}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Typography variant="body2" fontWeight={600}>
          {t(`content_check.method.${finding.method}`, { defaultValue: finding.method })}
        </Typography>
        <Chip size="small" variant="outlined" label={t(`content_check.layer.${finding.layer}`, { defaultValue: finding.layer })} />
        <Chip
          size="small"
          variant="outlined"
          color={finding.found ? "primary" : "default"}
          label={finding.found ? t("content_check.finding_found") : t("content_check.finding_not_found")}
        />
      </Stack>
      <Typography variant="body2" sx={{ mt: 0.75 }}>
        {finding.detail}
      </Typography>
      {(finding.record_id || finding.receipt_id || finding.score != null) && (
        <Stack spacing={0.25} sx={{ mt: 0.75 }}>
          {finding.record_id && (
            <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace", wordBreak: "break-all" }}>
              {t("content_check.record_id", { id: finding.record_id })}
            </Typography>
          )}
          {finding.receipt_id && (
            <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace", wordBreak: "break-all" }}>
              {t("content_check.receipt_id", { id: finding.receipt_id })}
            </Typography>
          )}
          {finding.score != null && (
            <Typography variant="caption" color="text.secondary">
              {t("content_check.score", { score: scoreLabel(finding.score) })}
            </Typography>
          )}
        </Stack>
      )}
    </Paper>
  );
}

function Explanation({ publicPage }: { publicPage: boolean }) {
  const { t } = useTranslation();
  const points = [
    t(publicPage ? "content_check.explain.what_public" : "content_check.explain.what"),
    t("content_check.explain.how"),
    t("content_check.explain.kept"),
    t("content_check.explain.no_proof"),
  ];
  return (
    <Box data-testid="content-check-explanation">
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        {t("content_check.explain.title")}
      </Typography>
      <Box component="ul" sx={{ m: 0, pl: 2.5, "& li": { mb: 0.5 } }}>
        {points.map((point) => (
          <Typography key={point} component="li" variant="body2" color="text.secondary">
            {point}
          </Typography>
        ))}
      </Box>
    </Box>
  );
}
