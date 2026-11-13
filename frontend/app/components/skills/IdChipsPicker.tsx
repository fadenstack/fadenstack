/**
 * A set of things picked by id from a searchable list, each shown as a chip: the agents a skill is assigned
 * to, the skills assigned to an agent (sdk/51 F01b). Only `offered` ids can be picked; a chip the caller may
 * not remove (`fixed`) stays whatever they do; a chip can lead to its page.
 */
import type { ReactNode } from "react";
import { Link as RouterLink } from "react-router";

import Autocomplete from "@mui/material/Autocomplete";
import Chip from "@mui/material/Chip";
import TextField from "@mui/material/TextField";

/** How one chosen id is shown. */
export interface ChipLook {
  label: string;
  /** Its page. */
  to?: string;
  /** Not removable here. */
  fixed?: boolean;
  /** `muted`: retired; `warning`: gone. */
  tone?: "muted" | "warning";
  /** Said on hover. */
  title?: string;
}

export interface IdChipsPickerProps {
  /** The ids chosen. */
  value: string[];
  /** The ids that may be picked. */
  offered: string[];
  onChange: (ids: string[]) => void;
  chip: (id: string) => ChipLook;
  /** An option's text, for the input and screen readers. */
  optionText: (id: string) => string;
  /** Whether an option matches what is typed (lower case, trimmed). */
  matches: (id: string, query: string) => boolean;
  renderOption: (id: string) => ReactNode;
  label: string;
  placeholder: string;
  noOptionsText: string;
  helperText?: string;
  disabled?: boolean;
}

export function IdChipsPicker({
  value,
  offered,
  onChange,
  chip,
  optionText,
  matches,
  renderOption,
  label,
  placeholder,
  noOptionsText,
  helperText,
  disabled,
}: IdChipsPickerProps) {
  const fixed = value.filter((id) => chip(id).fixed);
  const offeredSet = new Set(offered);
  // A chosen id is an option too, offered or not, so the value always matches one.
  const options = [...value.filter((id) => !offeredSet.has(id)), ...offered];

  return (
    <Autocomplete<string, true, true, false>
      multiple
      disableClearable
      filterSelectedOptions
      options={options}
      value={value}
      disabled={disabled}
      getOptionLabel={optionText}
      getOptionDisabled={(id) => !offeredSet.has(id)}
      filterOptions={(ids, { inputValue }) => {
        const query = inputValue.trim().toLowerCase();
        return query ? ids.filter((id) => matches(id, query)) : ids;
      }}
      onChange={(_, next) => onChange([...fixed.filter((id) => !next.includes(id)), ...next])}
      renderOption={(props, id) => {
        const { key, ...rest } = props;
        return (
          <li key={key} {...rest}>
            {renderOption(id)}
          </li>
        );
      }}
      renderValue={(ids, getItemProps) =>
        ids.map((id, index) => {
          const { key, onDelete, ...itemProps } = getItemProps({ index });
          const look = chip(id);
          const shared = {
            ...itemProps,
            size: "small" as const,
            label: look.label,
            title: look.title,
            variant: look.tone ? ("outlined" as const) : ("filled" as const),
            color: look.tone === "warning" ? ("warning" as const) : ("default" as const),
            sx: look.tone === "muted" ? { opacity: 0.6 } : undefined,
            // The chip may be a link: removing it must not also follow the link (MUI stops the click, the
            // browser would still go there).
            onDelete: look.fixed
              ? undefined
              : (event: React.SyntheticEvent) => {
                  event.preventDefault();
                  onDelete(event);
                },
          };
          return look.to ? (
            <Chip key={key} {...shared} component={RouterLink} to={look.to} clickable />
          ) : (
            <Chip key={key} {...shared} />
          );
        })
      }
      noOptionsText={noOptionsText}
      renderInput={(params) => (
        <TextField {...params} size="small" label={label} placeholder={placeholder} helperText={helperText} />
      )}
    />
  );
}
