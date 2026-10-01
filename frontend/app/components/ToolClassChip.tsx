/**
 * A tool's class -- what it does, which the tool modes act on: read, write,
 * destructive, or unclassified (always asks). One look everywhere a tool is shown.
 */
import { useTranslation } from "react-i18next";

import Chip, { type ChipProps } from "@mui/material/Chip";

export type ToolClass = "read" | "write" | "destructive" | "unknown";

const COLOR: Record<ToolClass, ChipProps["color"]> = {
  read: "success",
  write: "info",
  destructive: "error",
  unknown: "default",
};

export interface ToolClassChipProps {
  /** A class; anything else (or nothing) is unclassified. */
  value: string | null | undefined;
  /** Small inline chip, for lines of text. */
  dense?: boolean;
}

export function toolClassOf(value: string | null | undefined): ToolClass {
  return value === "read" || value === "write" || value === "destructive" ? value : "unknown";
}

export function ToolClassChip({ value, dense = false }: ToolClassChipProps) {
  const { t } = useTranslation();
  const toolClass = toolClassOf(value);
  return (
    <Chip
      size="small"
      variant="outlined"
      color={COLOR[toolClass]}
      label={t(`tools.class.${toolClass}`)}
      data-tool-class={toolClass}
      sx={dense ? { ml: 0.75, height: 18, fontSize: "0.68rem", verticalAlign: "middle" } : undefined}
    />
  );
}
