/**
 * Who may read a knowledge collection: everyone who uses the chat, or only
 * the people and groups it is open to. A restricted collection hides the
 * collections inside it too; the knowledge base's managers see every one.
 */
import { useTranslation } from "react-i18next";

import FormControlLabel from "@mui/material/FormControlLabel";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { principalKey, type PrincipalOption } from "~/api/principals";
import { ragLite, type RagLiteCollectionAccess, type RagLiteCollectionGrant } from "~/api/rag";
import { PrincipalList } from "~/components/PrincipalList";
import { PrincipalPicker } from "~/components/PrincipalPicker";

export interface CollectionAccessValue {
  access: RagLiteCollectionAccess;
  people: PrincipalOption[];
}

/** A collection's access as the form holds it. */
export function accessValueOf(collection?: {
  access?: RagLiteCollectionAccess;
  grants?: RagLiteCollectionGrant[];
}): CollectionAccessValue {
  return {
    access: collection?.access ?? "everyone",
    people: (collection?.grants ?? []).map((g) => ({
      type: g.principal_type,
      id: g.principal_id,
      label: g.label ?? g.principal_id,
    })),
  };
}

/** The form's access as the API takes it (an open collection has no grants). */
export function accessPayloadOf(value: CollectionAccessValue): {
  access: RagLiteCollectionAccess;
  grants: RagLiteCollectionGrant[];
} {
  return {
    access: value.access,
    grants:
      value.access === "restricted"
        ? value.people.map((p) => ({ principal_type: p.type, principal_id: p.id }))
        : [],
  };
}

export function CollectionAccessFields({
  value,
  onChange,
  disabled,
}: {
  value: CollectionAccessValue;
  onChange: (value: CollectionAccessValue) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const taken = new Set(value.people.map((p) => principalKey(p.type, p.id)));
  return (
    <Stack spacing={1} data-collection-access={value.access}>
      <Typography variant="subtitle2">{t("rag_lite.access.title")}</Typography>
      <RadioGroup
        value={value.access}
        onChange={(e) => onChange({ ...value, access: e.target.value as RagLiteCollectionAccess })}
      >
        <FormControlLabel
          value="everyone"
          control={<Radio size="small" />}
          label={t("rag_lite.access.everyone")}
          disabled={disabled}
        />
        <FormControlLabel
          value="restricted"
          control={<Radio size="small" />}
          label={t("rag_lite.access.restricted")}
          disabled={disabled}
        />
      </RadioGroup>
      {value.access === "restricted" && (
        <Stack spacing={1}>
          <PrincipalPicker
            search={(q) => ragLite.findPrincipals(q)}
            exclude={taken}
            placeholder={t("rag_lite.access.add")}
            disabled={disabled}
            onPick={(p) => onChange({ ...value, people: [...value.people, p] })}
          />
          <PrincipalList
            items={value.people}
            emptyText={t("rag_lite.access.nobody")}
            onRemove={
              disabled
                ? undefined
                : (p) =>
                    onChange({
                      ...value,
                      people: value.people.filter((q) => principalKey(q.type, q.id) !== principalKey(p.type, p.id)),
                    })
            }
          />
        </Stack>
      )}
      <Typography variant="caption" color="text.secondary">
        {t("rag_lite.access.hint")}
      </Typography>
    </Stack>
  );
}
