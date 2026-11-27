Real `dscl` output, captured read-only on macOS 27.2.1 (27 November 2026) from a Mac where
the A1 installer had created `_fadenagent` (UID/GID 450), plus the stock system account
`_oahd`. The only edit: the two machine-specific GeneratedUIDs of `_fadenagent`'s user and
group are replaced with zeros-based placeholders. Nothing secret is in them (`Password` is
`*`; `accountPolicyData` holds only a creation time).

| File | Command |
|---|---|
| `users-fadenagent.plist` / `.txt` | `dscl -plist . -read /Users/_fadenagent` / `dscl . -read /Users/_fadenagent` |
| `groups-fadenagent.plist` / `.txt` | the same for `/Groups/_fadenagent` |
| `users-oahd.plist` / `.txt` | the same for `/Users/_oahd` |
| `search-read-subset.plist` | `dscl -plist /Search -read /Users/_fadenagent UniqueID IsHidden UserShell` |
| `oahd-missing-attr.plist` | `dscl -plist . -read /Users/_oahd IsHidden UniqueID` (no IsHidden: absent, exit 0) |
| `search-users-uniqueid-450.txt` | `dscl /Search -search /Users UniqueID 450` |
| `search-groups-pgid-450.txt` | `dscl /Search -search /Groups PrimaryGroupID 450` |
| `search-users-recordname.txt` | `dscl /Search -search /Users RecordName _fadenagent` |
| `search-users-uniqueid-451-none.txt` | `dscl /Search -search /Users UniqueID 451` (no match: empty, exit 0) |

A record that does not exist: `dscl /Search -read /Users/<none> …` exits 56 with
`DS Error: -14136 (eDSRecordNotFound)` on stderr (with or without `-plist`).
