import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

const DEFAULT_SWAGGER_URL = import.meta.env.VITE_LLM_PORT_API_DOCS_URL ?? "http://localhost:8001/api/docs";

export default function ApiDocsPage() {
  const { t } = useTranslation();
  const [draftUrl, setDraftUrl] = useState(DEFAULT_SWAGGER_URL);
  const [activeUrl, setActiveUrl] = useState(DEFAULT_SWAGGER_URL);

  const normalizedUrl = useMemo(() => draftUrl.trim(), [draftUrl]);

  return (
    <Stack spacing={1.5}>
      <Paper sx={{ p: 1.5 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ xs: "stretch", md: "center" }}>
          <TextField
            size="small"
            fullWidth
            label={t("agents_api_docs.endpoint_url")}
            value={draftUrl}
            onChange={(event) => setDraftUrl(event.target.value)}
          />
          <Button
            variant="contained"
            onClick={() => {
              if (normalizedUrl) {
                setActiveUrl(normalizedUrl);
              }
            }}
          >
            {t("agents_api_docs.load")}
          </Button>
          <Button variant="outlined" href={activeUrl} target="_blank" rel="noopener noreferrer">
            {t("agents_api_docs.open_new_tab")}
          </Button>
        </Stack>
      </Paper>
      <Paper sx={{ p: 1, height: "72vh", minHeight: 520 }}>
        <Box
          component="iframe"
          title="api-docs"
          src={activeUrl}
          sx={{
            width: "100%",
            height: "100%",
            border: 0,
            borderRadius: 1,
            bgcolor: "background.paper",
          }}
        />
      </Paper>
      <Typography variant="caption" color="text.secondary">
        {t("agents_api_docs.note")}
      </Typography>
    </Stack>
  );
}
