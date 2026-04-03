# Carecaller Project Retrospective

## Purpose of this document

This document explains what the project was built to do, the major issues we faced while building it, and how each issue was solved.

The goal is to make onboarding easy for new contributors and to preserve engineering context for future maintenance.

---

## 1) Project objective (plain language)

Carecaller automates a healthcare check-in phone call pipeline end-to-end:

1. Place/receive a Twilio call.
2. Stream live audio to Gemini for realtime conversation.
3. Record call audio and download MP3.
4. Transcribe with Whisper.
5. Build a speaker-labeled final transcript.
6. Normalize transcript into strict `[AGENT]` / `[USER]` format.
7. Extract answers for 14 canonical healthcare questions.
8. Append structured samples to `result.json`.

The larger objective was not only “make it work,” but make it **deterministic** enough for downstream JSON extraction and dataset building.

---

## 2) Timeline of major problems and resolutions

## 2.1 Deterministic extraction failures from transcript variation

### Problem
Extraction quality dropped when normalized text varied slightly:
- `surgery` vs `surgeries`
- `allergies` misheard as `LLDs`
- question variants not matching canonical wording

This caused wrong question-to-answer alignment in `qa_json` and `result.json`.

### Root cause
Downstream extraction was sensitive to exact question phrasing, but the normalized transcript still contained conversational variation.

### Solution
- Strengthened normalization prompt rules around canonical question structure.
- Enforced cleaner speaker-tagged output to reduce ambiguity.
- Added stricter guidance around question splitting and correction handling.

### Result
Question matching became more consistent and extraction errors reduced significantly.

---

## 2.2 Correction/re-ask handling was inconsistent

### Problem
When users corrected earlier answers (for example, weight or height), the normalized flow sometimes retained stale context or mixed old/new answers.

### Root cause
Natural conversation includes interruptions and corrections, but extraction needs one final resolved answer per canonical question.

### Solution
- Improved normalizer prompt instructions for “let me update that” patterns.
- Added strict handling guidance for re-asks so corrected values become authoritative.

### Result
Final extracted answer is much more likely to reflect the user’s latest correction.

---

## 2.3 Combined questions in one agent line

### Problem
Some agent lines had multiple canonical questions merged into one line (for example allergies + surgeries), causing answer shifts.

### Root cause
Merged lines looked human-readable but broke deterministic question sequencing.

### Solution
Added strict normalization rule:
- One canonical question per `[AGENT]` line.
- If input has combined canonical questions, split into separate lines.

### Result
Question ordering became machine-safe for downstream extraction.

---

## 2.4 Agent self-answer pattern corrupted mapping

### Problem
In cases like:
- agent asks allergies,
- agent itself says “No new allergies,”
- then asks surgeries,

…the next user answer (about surgery) incorrectly mapped to allergies.

### Root cause
The transcript implied a user answer without an explicit `[USER]` line.

### Solution
Prompt-level logic for **self-answer detection**:
- Split into `AGENT question -> USER implied binary answer -> AGENT next question` when safe.
- Only for simple yes/no style implied answers.

### Result
Reduced cross-question leakage in extracted responses.

---

## 2.5 Outcome classification missing from normalized transcript

### Problem
Downstream aggregation needed a call-level outcome label (`completed`, `scheduled`, etc.), but normalized outputs were inconsistent.

### Root cause
Outcome existed conceptually but wasn’t strictly enforced in normalized text contract.

### Solution
- Added strict first-line contract: `outcome=<label>`.
- Added output enforcement helper so malformed/absent outcome defaults safely.

### Result
Every normalized file has a deterministic outcome signal for dataset generation.

---

## 2.6 Prompt grounding source was wrong/incomplete

### Problem
Normalization quality depended on grounding context, but source selection changed over time and initially used the wrong context.

### Root cause
Grounding text was not explicitly tied to per-call conversation logs.

### Solution progression
1. Initial grounding from transcript itself.
2. Switched to matching file in `conversation/` by call key.
3. Final refinement: use **agent-only** `agent>` lines as grounding to stabilize canonical question wording.

### Result
Normalizer became better anchored to what the agent actually asked, without adding noisy user text.

---

## 2.7 No aggregate dataset builder existed

### Problem
Needed a single `result.json` in expected schema, but pipeline stopped at normalized + QA outputs.

### Solution
Created `build_result_json.py` to:
- Parse `normalized_transcript/*.normalized.txt`
- Merge `qa_json` responses
- Include call duration from recordings
- Set direction (currently outbound)
- Maintain `total_samples`

### Result
Automatic, repeatable dataset generation from artifact folders.

---

## 2.8 Call duration extraction reliability

### Problem
Duration could be missing or zero in some runs.

### Root cause
No reliable MP3 metadata path initially.

### Solution
- Added `mutagen` dependency.
- Implemented metadata-based duration extraction with warnings/fallbacks.

### Result
Duration values became stable and non-zero in normal cases.

---

## 2.9 Empty/invalid `result.json` caused crash

### Problem
Builder failed with JSON parse errors when `result.json` was empty (0 bytes) or invalid.

### Solution
Hardened base loader to safely fallback:
1. `result.json` if valid,
2. else `expected_result.json` if valid,
3. else default skeleton.

### Result
No crash on empty/corrupt base file.

---

## 2.10 “Start from zero, only future entries” requirement

### Problem
User required future-only appends, no historical backfill.

### Solution
Introduced `.result_build_state.json` to track processed normalized files.

Then improved first-run behavior:
- if state missing/corrupt, initialize state from current normalized files,
- do not backfill on that run,
- append only new future files.

### Result
Deterministic incremental behavior across runs and across fresh clones.

---

## 2.11 Runtime state file confusion (`.result_build_state.json`)

### Problem
Team concern: “What is this file? Should it be committed?”

### Solution
Defined policy:
- runtime local state,
- ignore in git,
- document startup behavior in README.

### Result
Cleaner repo + no confusion for cloners.

---

## 2.12 Twilio auth error (401 / 20003)

### Problem
`call.py` failed with authentication error.

### Root cause
In long-lived shells, stale environment variables can override `.env` values.

### Solution
Changed `call.py` dotenv loading to:
- `load_dotenv(override=True)`

### Result
Project `.env` reliably wins over stale shell variables.

> Note: if auth still fails after this fix, credentials likely need rotation or SID/token pairing is wrong.

---

## 3) Automation behavior achieved

By the end of this work, the pipeline can auto-chain:

1. recording callback received
2. recording downloaded
3. Whisper transcription
4. final transcript build
5. Gemini normalization
6. Q&A extraction
7. `result.json` update

Controlled through env toggles:
- `AUTO_TRANSCRIBE_RECORDINGS`
- `AUTO_BUILD_FINAL_TRANSCRIPT`
- `AUTO_NORMALIZE_TRANSCRIPT`
- `AUTO_SAVE_QA_JSON`
- `AUTO_UPDATE_RESULT_JSON`

---

## 4) Known limitations / future improvement ideas

1. Some extraction edge cases may still occur in highly noisy or off-script calls.
2. Direction is currently fixed to `outbound` in builder.
3. If you intentionally want historical rebuild, add an explicit `--backfill` mode.
4. Could add stricter integration tests for cross-step determinism.

---

## 5) Lessons learned

1. Free-form AI output needs strict contracts for machine pipelines.
2. Prompt design and deterministic post-processing both matter.
3. Incremental state tracking is essential for append-only dataset workflows.
4. Runtime files should be documented and intentionally ignored/tracked.
5. Reliability is mostly about handling edge cases, not just happy paths.

---

## 6) Quick glossary

- `conversation/`: raw call conversation logs (per call)
- `whisper_transcript/`: ASR output
- `final_transcript/`: speaker-attributed transcript
- `normalized_transcript/`: cleaned `[AGENT]/[USER]` transcript with `outcome=` first line
- `qa_json/`: canonical question-answer extraction
- `result.json`: aggregated dataset output
- `.result_build_state.json`: local incremental processing state
