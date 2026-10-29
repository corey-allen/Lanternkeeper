# Ingestion pipeline

Code: `services/api/src/lk_api/ingestion/` and `lk_api/worker.py`.

## Steps

1. **Upload** (`POST /adventures/{id}/documents`): requires `rights_acknowledged=true` and a
   `rights_status`; streams with a size cap (`LK_MAX_UPLOAD_MB`); checks the `%PDF-` signature;
   opens with PyMuPDF to reject unreadable or encrypted files and enforce `LK_MAX_PAGES`; computes
   SHA-256; refuses duplicates per owner; writes the original under a generated key
   (`originals/xx/<40 hex>`, mode 0600, atomic rename); enqueues a job. The original filename is
   display-only and never used as a path.
2. **Claim** (worker): `SELECT … FOR UPDATE SKIP LOCKED`; stale `RUNNING` jobs (>15 min) are
   re-queued for crash recovery.
3. **Verify**: re-hash the stored file; mismatch fails the job permanently (`HASH_MISMATCH`).
4. **Extract**: PyMuPDF `get_text("blocks")` per page, keeping block bboxes.
5. **OCR fallback**: pages with fewer than `LK_OCR_MIN_CHARS` characters go to the configured
   `OcrEngine` (`TesseractOcr` via PyMuPDF; `NoOcr`; `StaticOcr` for tests). OCR use and
   unavailability are recorded as warnings, never silently skipped.
6. **Normalise**: NFKC, de-hyphenate line breaks, collapse whitespace. Raw text is kept alongside.
7. **Passages**: one per paragraph block; the latest `AREA` heading carries across pages.
8. **Flag injection**: `lk_dm.safety.detect_prompt_injection`. Flagged passages are stored (for
   review) but excluded from entity descriptions, from approval evidence, and from model context.
9. **Extract candidates**: `HeuristicExtractor` (below). `ModelExtractor` exists with the same
   schemas and evidence rule but is not wired into the worker (licensing gate).
10. **Validate graph**: duplicates (fuzzy name match per type), conflicting facts between
    duplicates, reverse-direction duplicate connections, broken connection/edge references,
    candidates without evidence, invalid fields. Affected candidates are marked `UNCERTAIN`.
11. **Persist** in one transaction, after deleting every row previously derived from this
    document. That makes retries idempotent. Re-ingesting a document with approved candidates is
    refused (`DOCUMENT_HAS_APPROVED_ENTITIES`) to protect provenance.
12. **Failure handling**: errors are recorded as a code plus a sanitised message (no document
    text). Retryable errors are re-queued until `max_attempts`; others fail immediately.
13. **Review** (UI and API): edit (server-validated), approve, reject. Approval requires evidence,
    valid fields, unflagged evidence, and, for connections and edges, both endpoints approved.
    Only approval creates `adventure_entity` / `location_connection` / `adventure_edge` rows.

## Heuristic markup convention

The deterministic extractor reads a plain-text keyed-entry layout:

```
AREA 1: The Lantern Gate
<description paragraphs>
Exits: north to The Drowned Chapel; up to The Bell Loft (hidden)
NPC: Name - description. Disposition: wary. Motivation: .... Knows: Clue name.
MONSTER: Name - description. AC 11, HP 6, ATK +1, DMG 1d4[, INIT +0].
CLUE: Name - content. Reveal: search, WIS, normal. Reveals: Target; Other target.
SECRET: Name - content.      QUEST: Name - objective.
TREASURE/HAZARD/ENCOUNTER/STORY: Name - description. Trigger: ....   FACTION: Name - .... Goals: ....
```

Published layouts will mostly not follow this. For them the plan is model-assisted extraction
(after licensing review) or hand-annotation. Either way, nothing is authoritative until approved.

## Synthetic fixture

`scripts/generate_fixture.py` builds `fixtures/synthetic_adventures/lantern_under_saltmere.pdf`
(original content) and a manifest with expected results. It covers: ordinary text (pages 1–2),
page references, an image-only page for OCR fallback (page 3), a duplicate NPC with a conflicting
disposition, a broken exit reference (The Salt Cellar), a hidden exit, and an embedded instruction
aimed at the AI.

## Deletion

`DELETE /documents/{id}` removes the original file, any derived files, pages, passages,
candidates, warnings, jobs, and approved entities left without evidence. It is refused while a
started campaign uses entities derived from the document. `DELETE /adventures/{id}` removes all of
its documents and is refused while any campaign references the adventure. Backups made earlier
still contain deleted data until they are deleted.
