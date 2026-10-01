/**
 * Find a user or group to grant something to: type part of an e-mail address
 * or a group name, pick from what the server finds. Used wherever access is
 * given to people and teams (skills, agents).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Autocomplete from "@mui/material/Autocomplete";
import ListItemIcon from "@mui/material/ListItemIcon";
import TextField from "@mui/material/TextField";
import GroupIcon from "@mui/icons-material/Group";
import PersonIcon from "@mui/icons-material/Person";

import { principalKey, type PrincipalOption, type PrincipalType } from "~/api/principals";

/** How long typing pauses before the server is asked. */
const DEBOUNCE_MS = 250;
const MIN_QUERY = 2;

export function PrincipalIcon({ type }: { type: PrincipalType }) {
  return type === "group" ? <GroupIcon fontSize="small" /> : <PersonIcon fontSize="small" />;
}

export interface PrincipalPickerProps {
  /** The server's search: users and groups matching the text. */
  search: (query: string) => Promise<PrincipalOption[]>;
  onPick: (principal: PrincipalOption) => void;
  /** Keys (`principalKey`) already chosen: not offered again. */
  exclude?: Set<string>;
  /** Only these kinds (e.g. users for maintainers). */
  types?: PrincipalType[];
  placeholder?: string;
  disabled?: boolean;
}

export function PrincipalPicker({ search, onPick, exclude, types, placeholder, disabled }: PrincipalPickerProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<PrincipalOption[]>([]);

  useEffect(() => {
    const text = query.trim();
    if (text.length < MIN_QUERY) {
      setOptions([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      search(text)
        .then((found) => {
          if (!cancelled) setOptions(found);
        })
        .catch(() => {
          if (!cancelled) setOptions([]);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, search]);

  const offered = options.filter(
    (o) => (!types || types.includes(o.type)) && !exclude?.has(principalKey(o.type, o.id)),
  );

  return (
    <Autocomplete<PrincipalOption>
      options={offered}
      getOptionLabel={(o) => o.label}
      filterOptions={(x) => x}
      inputValue={query}
      onInputChange={(_, value) => setQuery(value)}
      value={null}
      disabled={disabled}
      onChange={(_, picked) => {
        if (!picked) return;
        onPick(picked);
        setQuery("");
      }}
      renderOption={(props, o) => (
        <li {...props} key={principalKey(o.type, o.id)}>
          <ListItemIcon sx={{ minWidth: 32 }}>
            <PrincipalIcon type={o.type} />
          </ListItemIcon>
          {o.label}
        </li>
      )}
      noOptionsText={query.trim().length < MIN_QUERY ? t("principals.search_hint") : t("principals.no_match")}
      renderInput={(params) => (
        <TextField {...params} size="small" placeholder={placeholder ?? t("principals.search")} />
      )}
    />
  );
}
