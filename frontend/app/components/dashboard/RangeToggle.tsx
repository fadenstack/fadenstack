/**
 * A segmented choice of window ("Today · 24 h · 7 d · 30 d"), as the
 * dashboard's cards have it. 44 px on touch.
 */
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";

const TOUCH = { "@media (pointer: coarse)": { minWidth: 44, minHeight: 44 } } as const;

export interface RangeToggleProps<T extends string> {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  ariaLabel: string;
}

export default function RangeToggle<T extends string>({ value, options, onChange, ariaLabel }: RangeToggleProps<T>) {
  return (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={value}
      onChange={(_, next: T | null) => next && onChange(next)}
      aria-label={ariaLabel}
    >
      {options.map((o) => (
        <ToggleButton key={o.value} value={o.value} sx={{ px: 1, py: 0.25, textTransform: "none", ...TOUCH }}>
          {o.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}
