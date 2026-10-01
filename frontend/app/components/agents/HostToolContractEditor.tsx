/**
 * The host tool contract: the tools the host application may offer this agent,
 * each with its class. A tool outside the contract is refused; a tool without a
 * class is unclassified and always waits for the user's approval.
 */
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";

import type { HostTool, ToolApproval, ToolRisk } from "~/api/agents";
import { ToolClassChip } from "~/components/ToolClassChip";

const RISKS: ToolRisk[] = ["read", "write", "destructive"];
const APPROVALS: ToolApproval[] = ["never", "ask", "always"];
/** The select's value for "not set". */
const UNSET = "";

export interface HostToolContractEditorProps {
  tools: HostTool[];
  onChange: (tools: HostTool[]) => void;
  readOnly?: boolean;
}

export function HostToolContractEditor({ tools, onChange, readOnly = false }: HostToolContractEditorProps) {
  const { t } = useTranslation();
  const names = tools.map((tool) => tool.name.trim());

  const update = (index: number, changes: Partial<HostTool>) =>
    onChange(tools.map((tool, i) => (i === index ? withoutUnset({ ...tool, ...changes }) : tool)));

  return (
    <Stack spacing={1}>
      {tools.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {t("agents.contract.empty")}
        </Typography>
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t("agents.contract.name")}</TableCell>
              <TableCell sx={{ width: 190 }}>{t("agents.contract.class")}</TableCell>
              <TableCell sx={{ width: 170 }}>{t("agents.contract.approval")}</TableCell>
              {!readOnly && <TableCell sx={{ width: 48 }} />}
            </TableRow>
          </TableHead>
          <TableBody>
            {tools.map((tool, index) => {
              const duplicate = tool.name.trim() !== "" && names.indexOf(tool.name.trim()) !== index;
              return (
                // eslint-disable-next-line react/no-array-index-key -- rows are edited in place; names change while typed
                <TableRow key={index}>
                  <TableCell>
                    {readOnly ? (
                      <Typography fontFamily="monospace" variant="body2">{tool.name}</Typography>
                    ) : (
                      <TextField
                        size="small"
                        fullWidth
                        value={tool.name}
                        error={duplicate}
                        helperText={duplicate ? t("agents.contract.duplicate") : undefined}
                        placeholder="read_page"
                        inputProps={{ "aria-label": t("agents.contract.name"), style: { fontFamily: "monospace" } }}
                        onChange={(e) => update(index, { name: e.target.value })}
                      />
                    )}
                  </TableCell>
                  <TableCell>
                    {readOnly ? (
                      <ToolClassChip value={tool.risk} />
                    ) : (
                      <TextField
                        select
                        size="small"
                        fullWidth
                        value={tool.risk ?? UNSET}
                        inputProps={{ "aria-label": t("agents.contract.class") }}
                        onChange={(e) => update(index, { risk: (e.target.value || undefined) as ToolRisk | undefined })}
                      >
                        <MenuItem value={UNSET}>{t("tools.class.unknown")}</MenuItem>
                        {RISKS.map((risk) => (
                          <MenuItem key={risk} value={risk}>
                            {t(`tools.class.${risk}`)}
                          </MenuItem>
                        ))}
                      </TextField>
                    )}
                  </TableCell>
                  <TableCell>
                    {readOnly ? (
                      <Typography variant="body2">{t(`agents.contract.approval_${tool.approval ?? "default"}`)}</Typography>
                    ) : (
                      <TextField
                        select
                        size="small"
                        fullWidth
                        value={tool.approval ?? UNSET}
                        inputProps={{ "aria-label": t("agents.contract.approval") }}
                        onChange={(e) =>
                          update(index, { approval: (e.target.value || undefined) as ToolApproval | undefined })
                        }
                      >
                        <MenuItem value={UNSET}>{t("agents.contract.approval_default")}</MenuItem>
                        {APPROVALS.map((approval) => (
                          <MenuItem key={approval} value={approval}>
                            {t(`agents.contract.approval_${approval}`)}
                          </MenuItem>
                        ))}
                      </TextField>
                    )}
                  </TableCell>
                  {!readOnly && (
                    <TableCell>
                      <IconButton
                        size="small"
                        aria-label={t("common.remove")}
                        onClick={() => onChange(tools.filter((_, i) => i !== index))}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {!readOnly && (
        <Button size="small" startIcon={<AddIcon />} sx={{ alignSelf: "flex-start" }} onClick={() => onChange([...tools, { name: "" }])}>
          {t("agents.contract.add")}
        </Button>
      )}
    </Stack>
  );
}

/** A select's "not set" is no key at all, as the server stores it. */
function withoutUnset(tool: HostTool): HostTool {
  const out = { ...tool };
  if (!out.risk) delete out.risk;
  if (!out.approval) delete out.approval;
  return out;
}
