# Licensing, copyright, and privacy review

**Status: OPEN. This is not legal advice and reaches no legal conclusions.** It records what the
project assumes, what is unknown, and what is blocked until a qualified person (counsel, or the
publisher, The Arcane Library) has reviewed it. Uncertainty is never read as permission.

Reviewed sources (to be re-read before any decision; terms change):

- Shadowdark RPG licensing FAQ: https://www.thearcanelibrary.com/pages/shadowdark-faq
- The Arcane Library product and licensing pages: https://www.thearcanelibrary.com/
- The Shadowdark RPG Third-Party License (current version as published by the publisher)

The project has **not** independently verified the current text of these documents in this
repository. The constraints below come from the owner's design document and must be checked against
the live documents.

## 1. Known constraints

| # | Constraint | Source | How the code respects it today |
|---|------------|--------|--------------------------------|
| K1 | The license and FAQ distinguish core-rule material that third parties may use from Cursed Scroll zine content, which is treated differently. | Design doc §2, FAQ | No Shadowdark or Cursed Scroll text, tables, or values are in the repository. Zines are only ever user uploads. |
| K2 | The FAQ discusses restrictions on derivative applications and GM tools; an AI DM is such a tool. | Design doc §2, FAQ | The Shadowdark rules profile is an empty placeholder (`shadowdark-unverified`); every mechanic is blocked. |
| K3 | Owning a PDF does not by itself authorise every use (extraction, indexing, transmission to an AI service). | Project principle | Upload requires an explicit acknowledgement; the recorded `rights_status` is metadata, never authorisation. |
| K4 | Copyrighted adventures, extracted text, maps, and artwork must not be bundled or redistributed. | Design doc §2 | `.gitignore` blocks PDFs except `fixtures/synthetic_adventures/`, which holds only original content. |
| K5 | Uploaded content must not be exposed publicly or shared automatically. | Design doc §2, §11 | Documents are `PRIVATE` (DB check constraint); no download route exists; exports exclude source text. |
| K6 | Sending content to a remote model is a disclosure to a third party. | Design doc §11 | Remote endpoints are refused unless `LK_AI_ALLOW_REMOTE=true`; the UI shows a permanent disclosure badge. |

## 2. Unresolved questions

1. **Rules implementation.** May a private, self-hosted tool encode Shadowdark mechanics (ability
   modifiers, check DCs, light rules, combat, classes, spells) as executable logic? Does the answer
   change if the values are typed in by the owner rather than shipped with the software?
2. **Distribution.** May the software (without any Shadowdark content) be distributed publicly if it
   contains a Shadowdark-named profile placeholder, or uses "Shadowdark" in its name or description?
   Is a "compatible with" statement permitted, and with what wording or logo rules?
3. **Cursed Scroll zines.** What processing of a lawfully purchased zine is permitted: private text
   extraction? indexing? summarisation by a model? Does the answer differ for the adventure content
   versus any rules content inside a zine?
4. **Third-party adventures.** For non-Arcane-Library adventures, each publisher's terms apply. Is a
   per-document rights record enough, or is a per-publisher policy needed?
5. **Remote AI providers.** Is transmitting excerpts of a purchased adventure to a remote model
   provider permitted? Does provider data retention or training use change the answer?
6. **Derived artifacts.** Are extracted entity summaries, embeddings, and generated narration
   derivative works of the source? Can they be exported or shared at all?
7. **Trademarks.** Product name, UI copy, and repository description: what references to Shadowdark
   are acceptable?
8. **Repository visibility.** The GitHub repository is public. Confirm that nothing in it (including
   the placeholder profile names and documentation) is a problem.

## 3. Features blocked pending review

| Feature | Blocked because | Current behaviour |
|---------|-----------------|-------------------|
| Any verified Shadowdark rule value or mechanic | Q1, Q7 | `shadowdark-unverified` raises `UNVERIFIED_RULE` for everything. |
| Shipping a pre-filled Shadowdark profile, class list, spell list, monster list, or tables | Q1, Q2 | Not present. |
| Public distribution, packaging, or a hosted service | Q2, Q7 | Self-hosted from source only. |
| Remote AI as a default | Q5 | Default provider is the offline fake; remote needs an explicit flag. |
| Model-assisted extraction of uploaded documents | Q3, Q5, Q6 | `ModelExtractor` exists but the worker uses the heuristic extractor only. |
| Sharing or exporting adventure graphs or extracted text | Q6 | No such endpoint; campaign export omits source text. |
| Embedding/vector indexes of source text | Q6 | Not implemented (PostgreSQL full-text search only). |
| Multi-user or shared campaigns over uploaded content | Q2, Q6 | Single-user by design; ownership checks on every object. |

## 4. Review checklist for any future distribution

- [ ] Re-read the current Third-Party License and FAQ; record version/date here.
- [ ] Obtain written guidance from the publisher or counsel on Q1–Q8; attach or summarise below.
- [ ] Confirm no Shadowdark/Cursed Scroll text, values, tables, art, or trade dress is in the repo,
      the built images, test fixtures, documentation, screenshots, or git history.
- [ ] Confirm the synthetic fixture and evaluation data remain original (`fixtures/`).
- [ ] Confirm uploads stay private: no public routes, exports exclude source text, deletion works.
- [ ] Confirm remote AI is opt-in, disclosed in the UI, and documented with provider retention terms.
- [ ] Review product name, README, and UI copy for trademark use.
- [ ] Decide which rules (if any) may be implemented, and record each in
      `docs/rules-coverage-matrix.md` with citation and licensing status before coding it.
- [ ] Re-run the full test suite and the evaluation harness.

## Decision log

| Date | Decision | By |
|------|----------|----|
| 2026-10-29 | Ship no Shadowdark values; use an original house-rule profile for development (ADR-0004). | Project default, pending owner review |
| 2026-10-29 | Remote AI disabled by default; fake provider default (ADR-0005). | Project default |
| 2026-10-29 | The owner's supplied books are the Western Reaches guides, which consolidate Cursed Scroll 1–6. Q3 (Cursed Scroll processing) therefore applies to them as well as to the zines. | Noted by project |
| 2026-10-29 | The owner's core rulebook carries a notice that prohibits resale and redistribution and permits copying for personal use only. Recorded as a constraint (K3, K4); whether a private rules profile derived from it falls within personal use is part of Q1. | Noted by project |
| 2026-10-29 | Proceed with Shadowdark rules sourced from the owner's own copy, held only in gitignored `private/` and never committed; Q1 stays open and distribution stays blocked (plan 0002). | Owner |
