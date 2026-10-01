/** Lines of a diff: added in green, removed in red, hunk headers in blue. */
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";

import { hasChanges, type DiffLine } from "~/lib/textDiff";

const MARK: Record<DiffLine["kind"], string> = { same: " ", added: "+", removed: "-", hunk: "" };
const COLOR: Record<DiffLine["kind"], string> = {
  same: "text.primary",
  added: "success.main",
  removed: "error.main",
  hunk: "info.main",
};

export interface DiffViewProps {
  lines: DiffLine[];
  /** Shown when nothing changed. */
  emptyText?: string;
  maxHeight?: number;
}

export function DiffView({ lines, emptyText, maxHeight = 480 }: DiffViewProps) {
  const { t } = useTranslation();
  return (
    <Paper
      variant="outlined"
      sx={{ p: 1.5, fontFamily: "monospace", fontSize: 13, whiteSpace: "pre-wrap", overflow: "auto", maxHeight }}
    >
      {!hasChanges(lines) ? (
        <Typography variant="body2" color="text.secondary">
          {emptyText ?? t("diff.no_changes")}
        </Typography>
      ) : (
        lines.map((line, i) => (
          // eslint-disable-next-line react/no-array-index-key -- lines have no identity but their place
          <Box
            key={i}
            component="div"
            sx={{ display: "flex", color: COLOR[line.kind], bgcolor: line.kind === "same" ? undefined : "action.hover" }}
          >
            {/* The sign in a gutter of its own: never read (or copied) as part of the line. */}
            <Box component="span" aria-hidden sx={{ flex: "0 0 2ch", userSelect: "none", opacity: 0.7 }}>
              {MARK[line.kind]}
            </Box>
            <Box component="span" sx={{ minWidth: 0 }}>
              {line.text || " "}
            </Box>
          </Box>
        ))
      )}
    </Paper>
  );
}
