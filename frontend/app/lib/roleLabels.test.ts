/** The usage auditor role is named in the reader's language; other roles keep their stored name. */
import i18n from "i18next";
import { describe, expect, it } from "vitest";

import { roleHint, roleLabel } from "./roleLabels";

const t = (key: string) => i18n.t(key);

describe("role labels", () => {
  it("names the usage auditor role and says what it is for, with its key", () => {
    expect(roleLabel("usage_auditor", t)).toBe("Usage auditor");
    expect(roleHint("usage_auditor", t)).toMatch(/^usage_auditor: .*Being a superuser does not give this\.$/);
  });

  it("keeps any other role's name as stored", () => {
    expect(roleLabel("auditor", t)).toBe("auditor");
    expect(roleLabel("Finance readers", t)).toBe("Finance readers");
    expect(roleHint("admin", t)).toBeNull();
  });
});
