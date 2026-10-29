# ADR-0010: Private rules profiles, and campaigns pinned to profile versions

**Status:** Accepted (2026-10-29)

**Context.** Shadowdark rule values come from the owner's own books and must stay out of the
public repository (plan 0002). Profile files will be edited as subsystems are verified; an edit
must never silently change a campaign in progress.

**Decision.**
- Private profiles are `*.profile.json` files in `LK_RULES_PROFILE_DIR` (default unset; dev uses
  `private/profiles/`, Compose mounts it read-only at `/data/profiles`). They are validated at
  startup and any invalid file stops the API. `VERIFIED` entries must cite a page and carry
  `verified_on`. Keys may not collide with built-in profiles.
- On startup every profile is mirrored into `rules_profile`; each distinct content hash is stored
  once, immutably, in `rules_profile_version`.
- A campaign references one `rules_profile_version` row and always plays with that copy.
  `GET /campaigns/{id}/rules-profile` reports when a newer version is loaded;
  `POST /campaigns/{id}/rules-profile/upgrade` switches explicitly (not during combat; re-accepting
  house rules when present) and appends a `RULES_PROFILE_UPGRADED` event with both hashes.
  Previously derived values (max HP, AC) are not recomputed.
- Any profile containing a `HOUSE_RULE` entry requires explicit acceptance.
- `scripts/profile_template.py` writes a blank, all-`UNVERIFIED` template into `private/profiles/`.

**Consequences.** Rule values from the owner's books end up in the database and therefore in
backups; backups must be kept as privately as the books. Tests use synthetic profiles only.
