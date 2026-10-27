/**
 * The users and groups something is given to, one row each: icon, name, an
 * optional control (an access level, a role) and remove.
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import DeleteIcon from "@mui/icons-material/Delete";

import { principalKey, type PrincipalOption } from "~/api/principals";

import { PrincipalIcon } from "./PrincipalPicker";

export interface PrincipalListProps<T extends PrincipalOption> {
  items: T[];
  /** Shown when there are none. */
  emptyText: string;
  /** Absent: the list is read-only. */
  onRemove?: (item: T) => void;
  /** A control at the end of the row, before remove. */
  renderControl?: (item: T) => ReactNode;
  /** A second line under the name (a reason, who and when). */
  renderSecondary?: (item: T) => ReactNode;
}

export function PrincipalList<T extends PrincipalOption>({
  items,
  emptyText,
  onRemove,
  renderControl,
  renderSecondary,
}: PrincipalListProps<T>) {
  const { t } = useTranslation();
  return (
    <List dense disablePadding>
      {items.map((item) => (
        <ListItem
          key={principalKey(item.type, item.id)}
          divider
          secondaryAction={
            onRemove && (
              <IconButton edge="end" size="small" aria-label={t("common.remove")} onClick={() => onRemove(item)}>
                <DeleteIcon fontSize="small" />
              </IconButton>
            )
          }
        >
          <ListItemIcon sx={{ minWidth: 32 }}>
            <PrincipalIcon type={item.type} />
          </ListItemIcon>
          <ListItemText
            primary={item.label}
            secondary={renderSecondary?.(item)}
            slotProps={{ primary: { noWrap: true } }}
          />
          {renderControl && <span style={{ marginRight: onRemove ? 40 : 0 }}>{renderControl(item)}</span>}
        </ListItem>
      ))}
      {items.length === 0 && (
        <ListItem>
          <ListItemText secondary={emptyText} />
        </ListItem>
      )}
    </List>
  );
}

/** Whether two lists name the same principals, in any order. */
export function samePrincipals(a: PrincipalOption[], b: PrincipalOption[]): boolean {
  if (a.length !== b.length) return false;
  const keys = new Set(a.map((p) => principalKey(p.type, p.id)));
  return b.every((p) => keys.has(principalKey(p.type, p.id)));
}
