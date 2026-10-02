/**
 * A page of its own beside the chat (the user's skills, their agents): the
 * chat layout gives none of the admin console's chrome, so the page brings
 * its width, padding and scrolling.
 */
import type { ReactNode } from "react";

import Box from "@mui/material/Box";

export function UserPageFrame({ children, maxWidth = 1200 }: { children: ReactNode; maxWidth?: number }) {
  return (
    <Box sx={{ maxWidth, mx: "auto", px: { xs: 2, md: 3 }, py: 3, height: "100vh", overflow: "auto" }}>{children}</Box>
  );
}
