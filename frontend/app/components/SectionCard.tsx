/** A titled part of a form or panel: an outlined card with a title, a line of help, and its fields. */
import type { ReactNode } from "react";

import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

export interface SectionCardProps {
  title: string;
  description?: string;
  /** Controls on the right of the title. */
  actions?: ReactNode;
  children: ReactNode;
}

export function SectionCard({ title, description, actions, children }: SectionCardProps) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between" gap={1}>
        <div>
          <Typography variant="subtitle1" fontWeight={600}>
            {title}
          </Typography>
          {description && (
            <Typography variant="body2" color="text.secondary">
              {description}
            </Typography>
          )}
        </div>
        {actions}
      </Stack>
      <Stack spacing={2} sx={{ mt: 1.5 }}>
        {children}
      </Stack>
    </Paper>
  );
}
