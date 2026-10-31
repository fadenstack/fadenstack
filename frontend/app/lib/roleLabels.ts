/**
 * Built-in roles the console names in the reader's language. The stored name stays the role's key (Help
 * and the API use it, e.g. `usage_auditor`); pages show the label and, where there is room, the key and
 * what the role is for.
 */

/** Built-in roles with a label of their own (`roles.builtin_names.<name>`, `roles.builtin_hints.<name>`). */
export const NAMED_ROLES = ["usage_auditor"] as const;

type T = (key: string) => string;

function named(name: string): boolean {
  return (NAMED_ROLES as readonly string[]).includes(name);
}

/** A role's name to show: its label for a named built-in role, else the name as stored. */
export function roleLabel(name: string, t: T): string {
  return named(name) ? t(`roles.builtin_names.${name}`) : name;
}

/** What a named built-in role is for, with its key; null for any other role. */
export function roleHint(name: string, t: T): string | null {
  return named(name) ? t(`roles.builtin_hints.${name}`) : null;
}
