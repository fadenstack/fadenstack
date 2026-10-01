/**
 * A list of short values typed one by one (names, patterns, ids): each value
 * a chip, Enter or a comma adds one, suggestions when there are any.
 */
import Autocomplete from "@mui/material/Autocomplete";
import Chip from "@mui/material/Chip";
import TextField from "@mui/material/TextField";

export interface ChipListFieldProps {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  /** Values to pick from; anything else can be typed. */
  suggestions?: string[];
  helperText?: string;
  placeholder?: string;
  disabled?: boolean;
}

/** The values typed: trimmed, without blanks or repeats. */
export function cleanValues(values: string[]): string[] {
  return [...new Set(values.flatMap((v) => v.split(",")).map((v) => v.trim()).filter(Boolean))];
}

export function ChipListField({
  label,
  value,
  onChange,
  suggestions = [],
  helperText,
  placeholder,
  disabled,
}: ChipListFieldProps) {
  return (
    <Autocomplete<string, true, false, true>
      multiple
      freeSolo
      autoSelect
      options={suggestions}
      value={value}
      disabled={disabled}
      onChange={(_, next) => onChange(cleanValues(next))}
      renderValue={(values, getItemProps) =>
        values.map((v, index) => {
          const { key, ...itemProps } = getItemProps({ index });
          return <Chip key={key} size="small" label={v} {...itemProps} />;
        })
      }
      renderInput={(params) => (
        <TextField {...params} size="small" label={label} helperText={helperText} placeholder={placeholder} />
      )}
    />
  );
}
