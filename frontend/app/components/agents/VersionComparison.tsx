/** What changed from one version of an agent to another, field by field. */
import { useTranslation } from "react-i18next";

import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type { AgentDefinition } from "~/api/agents";
import { DiffView } from "~/components/DiffView";
import { diffLines } from "~/lib/textDiff";

import { changedFields, cleanDefinition, type DefinitionField } from "./agentDefinition";

/** A field as lines of text: prose as it is, everything else as indented JSON. */
function asText(definition: AgentDefinition, field: DefinitionField): string {
  const value = definition[field];
  if (typeof value === "string") return value;
  if (value === null) return "";
  return JSON.stringify(value, null, 2);
}

export interface VersionComparisonProps {
  before: AgentDefinition;
  after: AgentDefinition;
}

export function VersionComparison({ before, after }: VersionComparisonProps) {
  const { t } = useTranslation();
  const fields = changedFields(before, after);
  if (fields.length === 0) {
    return <Typography color="text.secondary">{t("agents.versions.no_changes")}</Typography>;
  }
  const [a, b] = [cleanDefinition(before), cleanDefinition(after)];
  return (
    <Stack spacing={2}>
      {fields.map((field) => (
        <Stack key={field} spacing={0.5}>
          <Typography variant="subtitle2">{t(`agents.field.${field}`)}</Typography>
          <DiffView lines={diffLines(asText(a, field), asText(b, field))} maxHeight={360} />
        </Stack>
      ))}
    </Stack>
  );
}
