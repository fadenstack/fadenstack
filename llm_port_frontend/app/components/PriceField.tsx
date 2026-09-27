/**
 * A price, in USD per million tokens -- the way providers publish them.
 *
 * The field keeps what is typed as text and reports the number it reads. It
 * used to be a number input bound to the parsed value: every keystroke was
 * parsed and written back, so "0." became "0" and a price like 0.15 could not
 * be typed at all ("0.00015" came out as "000015"). A comma is read as the
 * decimal point, as a German keyboard types it.
 *
 * The catalogue stores prices per 1K tokens (``price_catalog``); the helpers
 * below convert, so only this page's unit changes.
 */
import { useEffect, useState } from "react";

import TextField, { type TextFieldProps } from "@mui/material/TextField";
import InputAdornment from "@mui/material/InputAdornment";

const PRICE = /^\d*([.,]\d*)?$/;

/** The number a price field's text stands for; null when empty or not a number. */
export function parsePrice(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed || !PRICE.test(trimmed)) return null;
  const value = Number(trimmed.replace(",", "."));
  return Number.isFinite(value) ? value : null;
}

/** Per 1K tokens (as stored) to per 1M (as shown), without float dust. */
export function perMillion(per1k: number | string | null | undefined): number | null {
  if (per1k === null || per1k === undefined || per1k === "") return null;
  const value = Number(per1k);
  return Number.isFinite(value) ? Number((value * 1000).toPrecision(12)) : null;
}

/** Per 1M tokens (as typed) to per 1K (as stored). */
export function perThousand(perMillionValue: number): number {
  return Number((perMillionValue / 1000).toPrecision(12));
}

/** A per-1M price for reading: no trailing zeros, at most 6 decimals. */
export function formatPerMillion(per1k: number | string | null | undefined): string {
  const value = perMillion(per1k);
  if (value === null) return "—";
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 });
}

type PriceFieldProps = Omit<TextFieldProps, "value" | "onChange" | "type"> & {
  /** Per 1M tokens; null for "no price". */
  value: number | null;
  onChange: (value: number | null) => void;
};

export function PriceField({ value, onChange, slotProps, ...rest }: PriceFieldProps) {
  const [text, setText] = useState(value === null ? "" : String(value));

  // A new value from outside (another entry opened): show it. What the user
  // is typing is left alone -- "0." and 0 are the same number.
  useEffect(() => {
    setText((current) => (parsePrice(current) === value ? current : value === null ? "" : String(value)));
  }, [value]);

  return (
    <TextField
      {...rest}
      value={text}
      onChange={(event) => {
        const next = event.target.value;
        if (next.trim() !== "" && !PRICE.test(next.trim())) return; // digits and one decimal point only
        setText(next);
        onChange(parsePrice(next));
      }}
      slotProps={{
        ...slotProps,
        htmlInput: { inputMode: "decimal", autoComplete: "off", ...(slotProps?.htmlInput as object) },
        input: {
          startAdornment: <InputAdornment position="start">$</InputAdornment>,
          ...(slotProps?.input as object),
        },
      }}
    />
  );
}
