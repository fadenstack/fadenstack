/**
 * A page's title row, as the dashboard has it: title and a line under it on
 * the left, the page's controls on the right -- and on a phone the controls
 * wrap under the title instead of running off the screen.
 */
import type { ReactNode } from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

export interface PageHeaderProps {
  title: string;
  subtitle?: string;
  /** Controls on the right: a range, refresh, export, … */
  actions?: ReactNode;
}

export default function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      justifyContent="space-between"
      alignItems={{ sm: "flex-start" }}
      gap={1.5}
    >
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="h5" component="h1">
          {title}
        </Typography>
        {subtitle && (
          <Typography variant="body2" color="text.secondary">
            {subtitle}
          </Typography>
        )}
      </Box>
      {actions && (
        <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
          {actions}
        </Stack>
      )}
    </Stack>
  );
}
