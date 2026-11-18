# Automated reviewer: the review pipeline, in shadow mode

**Status:** design, revision 5.7 (2026-11-15). Revision 4.1 was reviewed and approved for implementation. Revision 4.2 added what was settled while building, revision 5 records three decisions the maintainer made on 15 November, and revision 5.1 records the faults that two independent reviews of the build found that day. **The spec's reviewer has reviewed the build and revisions 4.2, 5 and 5.1.** It approved 4.2, and approved the build to go on to the go-live steps once four faults it found were put right. Revision 5.2 records those fixes and some smaller ones, revision 5.3 the maintainer's answers to the two questions of policy the review raised, revision 5.4 his decision to run the panel at its deepest setting, and revisions 5.5 to 5.7 the direction he set for the finished design, which changes nothing that is built. Section 16 lists every change; 5.2 to 5.7 are its last six tables and have not themselves been reviewed.
**Scope:** process and code only. No scientific knowledge is needed to implement it.
**How to read it:** the numbered sections were written for revision 4. Where one of them disagrees with the section "Revision 5", revision 5 is what the code does. Section 16 lists every change and why.

## Revision 5: three decisions by the maintainer (15 November 2026)

**1. The reviewer runs at submission, and the maintainer may read its verdict before he decides.**

- Before: it ran only after the maintainer's decision, so that the decision was made without seeing it.
- Now: every run looks for proposals that have no complete result and reviews them, decided or not. A proposal is one that the submission bot opened. An issue typed straight into GitHub by anyone else is never reviewed, whatever it looks like, because only the bot's issues have been through sign-in, the template and the blind review.
- The record for a proposal is written with no decision in it. When the maintainer decides, a later run adds the decision to the same record and asks no model again. Each record says whether the verdict was posted before the decision.
- The scoreboard has a section, "Waiting for the maintainer's decision", that lists each undecided proposal with the gate's and the panel's verdict and a link to the full record. This is where the maintainer looks before he reviews.
- The workflow starts every hour once the schedule is on. A first job with no model key takes a quick look, and the job that asks the models is skipped when there is nothing to do.
- **What this costs, stated plainly:** a decision made after reading a verdict is not independent of it. Agreement between the two will be higher than a blind test would show, and cannot by itself prove the reviewer is right. The scoreboard says so at the top and counts, for every group, how many decisions came after the verdict was posted. What still carries weight is every case where the maintainer read a unanimous verdict and decided otherwise.
- **The maintainer's reference review.** He continues to review every proposal himself with his own review by the most capable models, and will until the automated reviewer matches it.
- **A blind share: not for now** (the maintainer's decision, revision 5.3). The suggestion was to keep a share of proposals unreviewed until the maintainer has decided, so that there is a blind measure beside the open one. He intends to move to blind review later. Section 9 says what waits on it.
- **A consequence for publishing:** if the repository is public, a contributor can read the reviewer's verdict on their proposal before the maintainer has decided. Whether the repository is public from the start is a decision for go-live.

**2. The name is the automated reviewer.**

- The repository is `OpenIslet/automated-reviewer`. "Shadow" is its mode today, not part of its name, so nothing is renamed when a part of it goes live.
- In the code: the package `reviewer/`, the settings folder `settings/`, the entry point `scripts/automated_reviewer.py`, the workflow `automated-reviewer.yml`, the environment `automated-reviewer`, and the switch `AUTOMATED_REVIEWER_ENABLED`.

**3. The stated aim is that every kind of outcome is handled automatically in the end.**

- Before: changing the blueprint, and adding to the Idea Graveyard, could never be handed over.
- Now: no outcome is barred for ever. The public plan (the README) said the reviewer was meant to take over one kind of outcome at a time, each only after it had matched the maintainer. **Changed in 5.7:** nothing is handed over until all four levels, and the full system, have proved themselves.
- **This grants nothing.** `mode: shadow` is still the only mode the code accepts, every authority setting must still be `shadow`, and the list of actions is still always empty. Handing over any outcome remains a separate design with its own review (section 15).
- The two outcomes that were barred are the ones where a wrong automatic decision does the most harm. Since revision 5.3 they are held to a stricter test, and the panel may never decide them alone: section 9.

**The maintainer's own proposals** (his decision, 15 November). In shadow mode they are not reviewed: `reconcile_self_decided` stays `false`. He both writes and decides them, so they cannot count as a test, they already go through his own review by the most capable models, and they are the longest and so the most expensive to review. Each is recorded in the log as left out, with the reason, and the README says so. A manual run can still name one. When the reviewer is given authority over any kind of outcome, his proposals are to go through it like everyone else's, and the setting is switched on as part of that change.

**Four levels, and no maintainer at the end** (the maintainer's direction, 15 November; revisions 5.5 to 5.7). The finished design has four levels: the gate (three small models), the panel (three stronger models), a frontier review by the most capable model from each of three providers, and an editor, one of the most capable models, which drafts the change to the blueprint. The numbered sections call the first two "stages" and the third "the third layer".

- A proposal must pass every level. **Two of a level's three models decide** (revision 5.6): a proposal passes when at least two pass it, and is stopped when at least two say stop. If one gives no answer and the other two disagree, it is passed up. The editor at level 4 is the final gate and is given every review: when level 3 is split two to one on changing the blueprint, or on ruling out a dead end, the editor decides. The person who sent a stopped proposal is told the outcome and why.
- Each level is given the reviews of the levels before it, including the review of a model that was outvoted, so a later level can take up what that model saw. Inside a level no model sees another's answer.
- **How it gets there** (revision 5.7). The system is built in shadow, one level at a time: levels 1 and 2 run today, level 3 is added next, then level 4. Each new level starts in shadow and has to prove itself there. A level in shadow has no effect on any proposal, so a new one may be added while the earlier ones are still being measured. Nothing is handed over level by level, or outcome by outcome. When all four levels have proved themselves, and the full system has, it is automated: first with the maintainer able to overrule it, then alone. This replaces the four steps of 5.5 (shadow, advises, acts, alone), which each level was to climb on its own.
- Once the whole system has proved itself the reviewer runs with no maintainer deciding. An owner keeps one switch that stops it, can undo a change, and decides nothing else about any proposal.
- **This grants nothing and designs nothing.** Levels 3 and 4 are not designed or built, `mode: shadow` is still the only mode the code accepts, and each step towards this is a separate design with its own review (section 15).
- Three principles in section 2 hold as written today and will have to change on the way: principle 2 (the maintainer's decision is the only one that counts), principle 3 (stages do not gate each other: in the finished design a stopped proposal goes no further) and principle 4 (no model sees another model's work: this will hold inside a level only).
- The README shows the plan, with a diagram and the models at each level.

## 1. Goal

Run the whole future automated pipeline on every proposal, record what each stage *would* have done, compare that with the maintainer's real decision, and publish the comparison. The results decide, one outcome type at a time, what can later run without a person.

The pipeline has two stages:

- **Stage A, gate:** three small, fast models each say whether a submission is spam.
- **Stage B, panel:** three stronger models from three labs each predict the outcome the maintainer will record.

**Not in scope:** no model output changes a label, closes an issue, edits the blueprint or blocks a submission. The contributor flow does not change.

**Built to be switched on, not switched on.** The trial is the real pipeline with its authority set to none. Section 15 lists the places where authority would later attach, so that turning one outcome type on is a small, reviewed change and not a rebuild.

## 2. Principles

1. **Shadow means no authority.** Every result is a record, never an action.
2. **The maintainer's decision is the only one that counts.** Since revision 5 the reviewer runs when a proposal is submitted, and the maintainer may read its verdict first. Each record says whether the complete verdict was posted before he decided. (Before revision 5 this principle was "the maintainer decides blind".)
3. **Stages do not gate each other.** The panel runs even when the gate says spam, so the trial can measure what the gate would have wrongly blocked.
4. **Identical treatment, and no model sees another model's work.** Every panel model gets the same input, prompt and output format. Gate models get the same input and question, and their answers are normalised. No model sees another model's output. That includes the contributor's own pre-submission blind review, which is inside every proposal and is removed before any model reads it.
5. **Only a proposal the submission bot opened can lead to a paid call.** An issue typed straight into GitHub never does, whoever labels it. The script enforces this itself, whatever started the run. (Before revision 5 the rule was that only a recorded decision could.)
6. **Everything except the keys is public:** prompts, configuration, code and results.
7. **Only prospective results count.** A result counts towards graduation only if the proposal was submitted after the configuration that judged it was frozen.
8. **Identity-blind.** Models do not see who submitted a proposal, or which AI helped write or review it.
9. **No money figures in public.** Caps and reports are in calls and tokens, in the repository, the log and the scoreboard alike.

## 3. How it runs

### Two repositories

| Name in the configuration | Repository | Role |
|---|---|---|
| `source_repo` | `OpenIslet/openislet` | Where proposals are (its issues) and where the blueprint files are. The trial only reads it, through GitHub's public API. Nothing is added to it. |
| `home_repo` | `OpenIslet/automated-reviewer` (new, public) | The workflow, the script, the configuration, the log and the scoreboard. |

The trial does not live in the source repository, for four reasons found in review:

- Every release overwrites the source repository from an allowlisted export, so files added there by hand are deleted by the next release.
- Every issue there takes the next public proposal number, so a log issue or a scoreboard issue would use one up.
- The release scanner for that repository rejects model names, which this configuration must contain.
- Each scheduled run that uses a protected environment adds a deployment record to its repository's page.

The workflow's own token can read a public repository's issues, events and files, so no extra access token is needed.

### Files, all in the home repository

- `.github/workflows/automated-reviewer.yml`
- `scripts/automated_reviewer.py` (the entry point) and the `reviewer/` package it runs
- `settings/config.yml` (repositories, models, settings, caps, allowed deciders, issue numbers, file patterns)
- `settings/outcomes.yml` (the decision labels and their definitions)
- `settings/prompts/` (gate and panel instructions)
- `settings/requirements.lock` (dependencies pinned with hashes)
- `tests/fixtures/reviewer/` (synthetic junk and adversarial submissions, and made-up proposals in both real body shapes for the offline tests)
- `tools/fetch_local_copies.py` (saves copies of real public proposals into a local folder that is never published, for checking the code against real data)

### The workflow is a reconciler

Every run does the same job: **find every submitted proposal that has no complete result for the current configuration and review them up to the caps, undecided ones first and oldest first within each group; and add the maintainer's decision to any reviewed proposal that has been decided since.** A cap holds back reviews only: recording a decision asks no model, so it is never held back. A missed or failed run is harmless because the next one picks up everything outstanding. The same loop handles retries, cap overflow and outages.

### Triggers

| Trigger | What it does |
|---|---|
| `schedule`, every hour at an off-peak minute (`17 * * * *`, configurable) | A quick look for work; if there is any, review, record and rebuild the scoreboard |
| `workflow_dispatch` | Mode `reconcile`, `fixtures` or `scoreboard`; optional proposal numbers |

An hour keeps a verdict ready soon after a proposal arrives. The quick look holds no key and uses no environment, so an hour with nothing to do asks no model and leaves no deployment record.

There is deliberately **no issue-event trigger**:

- Nothing in an automated reviewer needs to be instant.
- The home repository receives no events from the source repository in any case.
- With no event trigger, nothing an outsider does can start a run.

Proposal numbers passed to a manual run can only narrow the set. Every guard below still applies to them.

A scheduled workflow is switched off by GitHub after 60 days without a push to its repository, and comments do not count as pushes. Starting a new log issue (section 7) is a push, because its number is committed to the settings and the old number moves to `earlier_log_issues`, whose records are still read. The scoreboard shows when it was last built, and a second schedule line rebuilds it once a day even when there was nothing to review, so a stopped schedule is visible within a day.

### Three jobs

0. **`find`** takes a quick look: is there a proposal to review, or a change to record? It holds no key and uses no environment. Permission: `contents: read` and `issues: read`. On the clock, the review job is skipped when it finds nothing. A run started by hand always goes on to the review job.
1. **`review`** holds the provider keys and makes the model calls.
   - References the `automated-reviewer` environment. Permission: `contents: read` and `issues: read` (the log is an issue, and reading it needs that permission while the repository is private). It can write nowhere.
   - The keys are exposed only to the single step that calls the providers, never to checkout or dependency installation.
   - Checkout uses `persist-credentials: false`.
   - Writes results to a file and uploads it as an artifact, including when the job fails part-way.
2. **`publish`** writes to the log and the scoreboard.
   - No environment, no provider keys. Permission: `issues: write`, which covers the home repository only.
   - Treats the results file as untrusted: validates it, sanitises it, then posts or edits comments, throttled to stay well inside GitHub's content-creation limits.
   - Runs even if `review` failed, so paid results are not lost. The review job saves its results after each proposal for the same reason.
   - It, and not the results file, says when a complete verdict was first posted.

All three jobs: one shared concurrency group with `cancel-in-progress: false`, a `timeout-minutes` value, and `if: github.repository == '<owner>/<repo>'` so forks never run them.

### What counts as a decided proposal

- An issue in the source repository, not a pull request, and not in the `skip_issues` list (issue 1, the founding release, is not a proposal).
- **Decision labels** are listed in `outcomes.yml`, never hard-coded: the seven `outcome:*` labels, `decision:needs-work`, and one new label, `outcome:spam`.
- The decision is the **first** decision label applied, read from the issue's label events, with its actor and time.
- One exception, since revision 5.2: a decision label that an allowed decider takes off again within five minutes, and replaces with a decision label, was a slip of the hand. It is not a decision. The label that replaced it is, and the record carries the flag `decision_label_corrected_at_once`. A label taken off later than that, or taken off and not replaced, still stands.
- That actor must be in the `allowed_deciders` list in the configuration. Use an explicit list of logins; do not use a permission lookup.
- If an issue later receives a second decision, only the first round is in the trial. Flag the rest.

The checks run in this order, on the issue's label events:

1. **Administrative closure.** The label `status:closed-triage`, applied by an allowed decider, records the issue as excluded. This label is used *instead of* a decision label, so it must be looked for first.
2. **No decision label from an allowed decider.** The issue is never touched. That covers bug reports opened from the other issue template.
3. **Self-decided.** Checked before the text is confirmed or parsed, so the maintainer's own proposals are recorded as self-decided and not as unparsed.
4. **Text confirmation and parsing** (section 4).

**Recorded and excluded, with no model call:**

| Case | How it is recognised | Recorded as |
|---|---|---|
| Set aside for an administrative reason | The label `status:closed-triage`, applied by an allowed decider | `excluded: administrative_triage` |
| The maintainer's own proposal | The issue's author, or the "Submitted by" login in its header, is the account that decided it | `excluded: self_decided` |
| Text that cannot be confirmed as submitted | See section 4 | `excluded: text_unverified` |
| A body that does not have the expected structure | See section 4 | `excluded: unparsed` |

Self-decided proposals cannot count towards graduation, so by default they make no calls: `reconcile_self_decided: false`. A manual run can name one explicitly as a sanity check; its result is then reported separately.

### Guards, checked before any model call

- Kill switch: a repository variable, `AUTOMATED_REVIEWER_ENABLED`.
- `mode` in the configuration must be `shadow`. The script stops at start-up on any other value (section 15).
- No complete result already exists for this proposal and configuration.
- Per-run limit (default 10 proposals), daily cap (default 20, UTC day), and a monthly cap on calls and on input and output tokens for each provider. All are computed from the log and apply to every mode. Only an explicit `override_caps` input on a manual run lifts them.
- Fixture runs count against the monthly caps but not the daily proposal cap.
- A maximum output-token setting on every call.
- A token count before each call, so oversize inputs are caught without a model call.
- A test asserts that no issue template applies a decision label.

### Configuration identity

Two levels, both computed and never typed by hand:

- **`policy_version`**: a hash of the outcome definitions, the prompts, the output schema, the list of context files, and the code that builds the input. Any change here starts a new prospective count.
- **`roster`**: for each model slot, the model ID requested, the version the provider reports back, and the settings used.

Every result records both. The scoreboard groups by `policy_version` and shows the roster within it. Section 9 says what happens when one provider replaces a model.

## 4. Input assembly and leakage rules

The trial is only valid if the models see what was submitted, and nothing that came after.

### The proposal, as the submission backend wrote it

A proposal's body has a fixed structure, in one of two shapes. The script parses that structure exactly. It never guesses: a body that does not parse is excluded as `unparsed`.

- **Opened by the submission bot.** The first line is "Submitted by @login (verified through GitHub)", and the last line is the signed submission marker.
- **Opened directly by the maintainer.** The first line says so, and there is no marker. These are self-decided and make no calls by default, but they are still parsed: each one appears in the prior-decisions index for later proposals.

| Part of the issue | Sent to models | Kept in the record |
|---|---|---|
| Title, with the leading `[IIP]` tag removed | yes | |
| "Submitted by" or "Opened directly by the maintainer" line, the "ORCID iD" line and the "Stated by the contributor" line | no | no |
| "AI tools (self-reported)" line | no | drafting AI and reviewing AI |
| "Written against" line | yes | |
| "Consent" line | no | |
| The **Draft** block, except its "AI tools used" section | yes | |
| The Draft's "AI tools used" section | no | |
| The **Blind review** block | **no** | the reviewer's verdict and the reviewing AI |
| The wet-lab readouts block, when present | yes | |
| The signed submission marker | no | the claim, the study identifiers and the body hash |

The blind review is another AI's full judgement of the proposal, with a verdict. It never reaches a model. Its verdict is recorded so the scoreboard can compare that single review with the panel.

Two rules for the parser, both found by checking real proposals:

- The Draft's "AI tools used" section runs from its own second-level heading to the next second-level heading or the end of the Draft block. Third-level headings do not end it: real proposals use them inside other sections.
- The blind review is removed as one whole block, after its verdict line is read. It cannot be recognised by its last section, which varies.

### Confirming the text is what was submitted

- **Fast path.** The marker at the end of a bot-opened issue holds a SHA-256 hash of everything above it. The script recomputes the hash the same way the backend does (line endings made plain, trailing space trimmed). If it matches, and the issue has no title-rename event before the decision, the text is as submitted. Contributors cannot edit an issue the bot opened, so this is strong evidence.
- **Fallback.** If the hash does not match, or there is no marker, read the body's edit history through GitHub's GraphQL API and use the oldest version. Title changes are not in that history; read them from the rename events. If a revision was deleted, or the history cannot be read, exclude the case as `text_unverified`.
- The script cannot check the marker's signature, by design: it never holds the backend's signing key.
- If the text or title changed before the decision, set `edited_before_decision` and report the case separately. Edits can carry cues, such as a rewritten body answering an objection or a retitle naming a duplicate.
- Record a SHA-256 hash of the text sent to the models, and which path confirmed it.

### Excluded from every input

- Every comment written by a person, including the maintainer's decision comment, which is posted seconds before the label.
- Every label applied at or after the decision, and the open or closed state.

### Automated checks from submission time

- The submission bot posts an "Automatic checks" comment within seconds of creating a proposal. It has two parts.
- **"The other checks" is included**, marked as an automated report: format, word matches against the Idea Graveyard, and whether quotes match the studies the project already holds.
- **"The basic rules" is left out.** One of its rows states the blind reviewer's verdict, and every other row is a rule the proposal must already have passed to exist.
- Include the comment only if it can be separated reliably: authored by the submission bot, starting with the fixed heading, within ten minutes of creation, before any human activity. If in doubt, leave it out.

### Repository context

- Read from the last commit on the source repository's default branch **before** the proposal's `created_at`. Never the current head, which may already contain the change the proposal asked for.
- Always included in full: the blueprint, the Stage 0 playbook and the Idea Graveyard file. Most real proposals turn on what the playbook already specifies; without it a model cannot tell a gap from something already there.
- File names change between versions (the Idea Graveyard file carries a version number), so the configuration holds a pattern for each file and the script resolves it at that commit.
- Read the files by commit, so a later change cannot alter what a given proposal is judged against. A file over 1 MB cannot be read through the ordinary contents request; read it as a raw blob.
- If no commit precedes the proposal, or a file is missing at that commit, record `context: unavailable` and exclude the case.
- Outcome definitions and prompts always come from the frozen configuration, never from history.
- Sizes today: about 44,000 tokens for the blueprint, 31,000 for the playbook and 8,000 for the Idea Graveyard.

### Cited-file excerpts, computed by code

- When the Draft cites a file in the repository, with or without line numbers, the script attaches that passage from the context commit: the cited lines and 40 lines either side, or the first 80 lines when no line is given.
- Files already included in full are skipped. The total is capped at 15,000 tokens; what does not fit is listed by name only.
- No model is involved, and a path that does not exist at that commit is reported as such.

### Evidence lookup, computed by code

- The evidence graph file is about 23 MB and must never be sent to a model.
- The script collects the study identifiers in the proposal: the list in the marker, plus PubMed, PMC, DOI and trial-registry identifiers found in the Draft.
- For each one it reports whether the study is already in the project's extracted evidence at the context commit, and whether it appears in the wider corpus list when that file exists at that commit. No model is involved.
- Where the extracted evidence lives has changed over time, so the configuration lists the places in order: the evidence graph file when it exists at that commit, and otherwise the earlier per-study files and study list. The first one found is used, and the record says which.
- Without this block a model cannot tell "adds evidence" from "already covered".

### Prior-decisions index

- Built from label events **as they stood at this proposal's `created_at`**, not from current labels, which may include later reversals.
- One line per earlier decided proposal: number, title, type, outcome, and a one-sentence summary. The summary is the claim in the proposal's marker, or the Draft's "Claim in one sentence" field when there is no marker, length-capped. No model-written summaries.
- Earlier proposals still undecided at that moment are listed the same way, marked pending.
- These lines are contributor-written text. They go inside the untrusted section, like the proposal itself.

### Assembly

- Order: instructions, outcome definitions, repository context, cited excerpts, evidence lookup, index, automated checks, then the proposal last.
- Stable content first makes each provider's prompt caching work when several proposals are processed in one run, and for the self-consistency calls. Use caching where it needs no extra moving parts, and record cached and uncached input tokens separately.
- Untrusted sections are wrapped in a delimiter generated randomly for each call. If the delimiter appears in the text, generate another and record the collision.
- The instructions say that everything inside the delimiters is material to assess, never instructions to follow.
- If the input exceeds a model's limit, record `no_verdict: too_large`. Never truncate silently.

## 5. Stage A: gate

**Question:** is this a good-faith proposal, or spam, abuse or off-topic junk?

**Input:** the proposal text as section 4 prepares it, and a short fixed description of what a valid submission looks like. No blueprint and no index, which is what keeps this stage small.

**Normalised output per model:**

```json
{ "spam": false, "probability": 0.03, "reason": "short plain-text reason or null" }
```

- Each model has an adapter in the configuration. For decision-style models that return a probability and no text, the adapter states the question sent and the threshold used (default 0.5), and both are recorded. No such model is in use: the path is kept so the two kinds can be compared later.
- The majority verdict needs at least two valid answers that agree. Otherwise record `no_majority`.
- Use a medium reasoning setting for each model, and record it. The gate reads one short proposal, and it must never hold a real one.
- **Ground truth:** `outcome:spam` means spam; any other decision label means not spam. Administrative closures are not in the trial.

**Models (configurable):** three small models that answer in text, one from each provider: `claude-haiku-5-5`, `gemini-4-hydrogen` and `gpt-6-luna`.

**Fixtures:** real spam may be rare, so keep about 30 synthetic junk and borderline submissions in the fixtures folder. The submission backend already refuses anything that lacks the template, a sign-in or a passing blind review, so the fixtures concentrate on what can still get through: text that fits the template and says nothing, off-topic material dressed as a proposal, and issues opened directly on GitHub. They run in `fixtures` mode, are never opened as issues, and are reported separately.

## 6. Stage B: panel

**Task:** predict which outcome the maintainer will record for this proposal.

**Models (configurable):** `gemini-4-neon`, `gpt-6.2-sol`, `claude-sonnet-5-5`.

**Output schema, identical for all three:**

```json
{
  "outcome": "one value from outcomes.yml",
  "confidence": 0.0,
  "covered_by": null,
  "reasons": ["plain-text strings"]
}
```

Rules:

- The allowed outcomes and their definitions come from `outcomes.yml`. It is written once from the project's published definitions; the prompt quotes it word for word.
- Use each provider's structured-output feature for types and the outcome list. Not every provider can enforce lengths or ranges, so apply those limits in the script, the same way for every model: keep the first three reasons, cut each to 300 characters, clamp confidence to 0–1. Trim; do not reject.
- `covered_by` is an integer or null. If it is not a number in the index, set it to null and flag it.
- Validate every response. On invalid output retry once, then record `no_verdict: invalid_output`.
- If a response is cut off at the output limit, record `no_verdict: truncated`. Do not retry it as invalid. Default output limit: 32,000 tokens, including reasoning.
- No tools and no web access for any model.
- A refusal or safety block is recorded as `no_verdict: refused` with the provider's category. **Never send a fallback-model setting and never retry on another model**, because that would mix models in the measurement.
- Set the reasoning or effort level explicitly for every model and record it (default `high`). Do not send sampling settings such as temperature; some current models reject them.
- Record the model version the API reports and check it is the model requested.
- Record input tokens (cached and uncached), output tokens and latency for every call.
- **Unanimous** means three valid verdicts that agree. A missing verdict makes the case incomplete, not unanimous.

**Self-consistency:** on every fifth proposal, make a second independent call to each panel model and record whether it gave the same outcome. A model's agreement with itself is the ceiling on its agreement with anyone else.

**Adversarial fixtures:** the fixtures folder also holds proposals that try to steer the verdict, including ones that imitate delimiters, fake index lines or a fake blind review. Each fixture declares its own context commit and submission time. They run only in `fixtures` mode.

**Failures:** retry a transient error up to three times within the run, without recording it. If a model still has no answer, leave only that model's slot pending. Later runs fill pending slots without repeating calls that succeeded. After three runs, record `no_verdict: error`.

**Notes for the Claude calls** (checked against the current API guidance; check again when coding):

- Use the model IDs exactly as written, with no date suffix, and record the model the response reports.
- Ask for the schema with structured outputs (`output_config.format`). Forcing a tool call is rejected by this model.
- Thinking cannot be switched off on the panel model. Leave the thinking setting out and control depth with `output_config.effort`, set explicitly.
- Stream the response and read the final message: a 32,000-token limit is too large for a plain request to finish inside the client's time-out.
- Check `stop_reason` for `refusal` before reading any content, and record the category from `stop_details`.
- Do **not** send the `fallbacks` setting, though it is the normal default for this model. A fallback would let a different model answer in this slot.
- Use the token-counting endpoint for the estimate before each call.

## 7. Recording

### Where results go

Results do **not** go on the contributor's issue, or anywhere in the source repository. A comment there would notify the contributor, look like a second judgement, and invite disputes that quote the models.

They go to two pinned, locked issues in the home repository, both identified by number in the configuration and never by a title search:

- **The log.** One comment per proposal per configuration, edited in place when pending slots are filled. Fixture results are recorded the same way, keyed by fixture ID. Start a new log issue each quarter, and commit its number to the configuration.
- **The scoreboard.** See below.

The workflow's own token can comment on a locked issue in its repository; outside users cannot. The maintainer should mute notifications on both.

The log is the durable record. If a proposal issue is later deleted, its log entry stays and the scoreboard marks the proposal as no longer available.

### Trust rule

Only comments authored by the workflow's own identity (`github-actions[bot]`, account type Bot) are read, both for the "already done" check and for the scoreboard. Anyone can write text that imitates the format, so author identity is the only thing that makes a record genuine.

### Format

- A header: **Automated reviewer, in shadow mode. This is not a decision and has no effect on any proposal.**
- A table of stage, model, verdict and confidence, built only from validated values and numbers.
- No contributor-written text, such as titles.
- The proposal is named by its number in code formatting, so GitHub does not create a cross-reference on the contributor's issue.
- The reasons inside a fenced code block whose fence is longer than any run of backticks in the text, so nothing a model writes can render as a link, image, mention, issue reference or markup.
- A hidden machine-readable block holding **base64-encoded** JSON. Raw JSON inside an HTML comment can be broken out of by text containing the comment terminator.
- A comment is limited to 65,536 characters. The trimmed reasons keep an entry far below that; the script checks the length before posting.

The JSON holds:

- Proposal number, text hash, how the text was confirmed, edit flags, context commit, `policy_version`, roster, run time.
- The decision at the time of the run: label, who applied it, when. For "already covered", the earlier proposal the maintainer named.
- Whether the proposal's author is the same account that decided it, and any exclusion with its reason.
- The drafting AI and the reviewing AI reported by the contributor, and the blind reviewer's verdict.
- For each model: ID, reported version, settings, threshold if any, verdict, status, attempts, tokens, latency.
- The recommendation each stage reached and the action taken, which in shadow mode is always none (section 15).

### Scoreboard

Rebuilt after every reconcile run from the trusted log comments only. It makes no model calls.

- The issue body holds the headline summary and the full tables for the **current** `policy_version` only, kept under 45,000 characters.
- Each retired `policy_version` gets one comment on the same issue, written once and not edited again.
- The same numbers are uploaded as a JSON file with each run, for anyone who wants to check them.

## 8. Metrics

**Ground truth**

- The ground truth is the maintainer's decision. Since revision 5 he may have read the verdict first; each record says whether he could have, and the scoreboard counts those cases.
- The scoreboard also reads the current label and lists every reversal separately, with its direction: towards the panel or away from it.
- For graduation, a case counts as agreement only if the panel matches **both** the original and the current decision.

**Coverage**

- Decided proposals against recorded results, listing every missing proposal and why (capped, pending, refused, too large, error, excluded and for what reason).
- A model that declines the hardest cases must not look better for it, so no-verdict cases stay in the denominators as "not unanimous".

**Baselines and calibration**

- A baseline that always predicts the most common outcome, so agreement figures have something to beat.
- A second baseline: the contributor's own blind review. Its verdict is not an outcome, so report how often each of its verdicts went with each outcome, beside the panel's figures.
- A calibration table for the recorded confidence values.

**Reported separately and excluded from headline numbers**

- Proposals whose author is the account that decided them.
- Retrospective runs (see section 9).
- Cases with unavailable context, text edited before the decision, or more than one decision round.

**Gate**

- False-hold rate: real proposals flagged as spam, per model and for the majority.
- Catch rate on real spam and on fixtures, reported separately.

**Panel, per model and per outcome type**

- Precision: when the model predicted outcome X, how often the maintainer recorded X.
- Recall, and a full confusion table.
- For "already covered": whether `covered_by` names the proposal the maintainer named, split by whether that earlier decision existed when this proposal was submitted. The maintainer records it on its own line in the closing comment, as `Same as: IIP-0010`, which matches the form the website's records already use. One earlier case was closed before this rule, in other words; a second pattern reads that one for retrospective runs only.
- No-verdict rate, split by cause.
- Self-consistency rate.
- Manipulation rate on the adversarial fixtures.
- Calls and tokens per review.

**Panel, combined**

- How often all three agree, and precision when they do.
- Which model matched the maintainer when they split.

**Critical misses**

- The panel is unanimous for any outcome other than `changed-blueprint`, and the maintainer recorded `changed-blueprint`.
- The panel is unanimous for `changed-blueprint`, and the maintainer recorded something else.
- The same two for `dead-end`, ruling out a dead end, which adds to the Idea Graveyard (revision 5.3).
- The same two counts per individual model, for information.

**Same-lab effect**

- Agreement split by whether the reviewer comes from the same lab as the reported drafting AI.

**Maintainer consistency (optional, recommended)**

- After four weeks or more, the maintainer re-decides ten old proposals without looking at the original outcome. This shows how much disagreement is simply the ceiling for any reviewer.

## 9. Graduation rule (proposed defaults)

**Prospective only.** Freeze a `policy_version`, then count only proposals submitted after that moment. Results from the back catalogue are for developing the prompts and are labelled retrospective. Prompts tuned on a set of cases look better on those cases than they really are, and newer models may have seen published decisions in training.

**Evaluated once, on a fixed sample.** For each outcome type, the test is run on the **first 50** prospective cases under one `policy_version` in which the panel was unanimous for that type. It is not a sliding window and is not re-checked each week, because repeated looks make a lucky pass likely.

The type becomes *eligible* for automatic handling if, in those 50:

- The maintainer's decision differed in at most 1.
- None was a critical miss as defined in section 8.
- No adversarial fixture for that type succeeded.

If the test fails, a new `policy_version` must pass on 100 cases, not 50.

**When a provider replaces one model.** Reaching 50 cases for one type will take many months, and across three providers at least one model is likely to be replaced in that time. A replacement must not silently throw the count away, and must not silently carry it over either:

- The `policy_version` is unchanged, and exactly one panel slot changes.
- Run the replacement on the last 25 prospective proposals already decided, and on every adversarial fixture.
- If it gives the same outcome as the model it replaces on at least 23 of the 25, with no critical miss and no successful fixture, record a continuity event and let the count continue. The scoreboard reports the cases before and after the change separately as well as together.
- Otherwise the count starts again.
- Two or more slots changing at once always starts the count again.

**The gate** becomes eligible to hold submissions only when the majority verdict has zero false holds across at least 100 real prospective proposals **and** catches at least 90% of the fixtures and of at least 20 real spam cases. If real spam never reaches 20 cases, the gate stays off, which is fine: without spam it is not needed.

What these numbers mean: zero disagreements in 50 cases still only shows the true error rate is below about 6% with 95% confidence, and one in 50 shows it is below about 9%.

**No outcome is barred for ever** (revision 5), **and two are held to a stricter test** (revision 5.3: the maintainer's decision, on the advice of the spec's reviewer). Changing the blueprint alters the reference design. Ruling out a dead end adds to the Idea Graveyard and steers later contributors away from a route. For these two outcome types:

- **The panel never decides alone.** Passing the count lets the panel pass a proposal up to the third layer of review, and nothing more. The third layer is planned and not designed.
- **No disagreement in the 50**, where the other types allow one.
- **A miss on either side of either type is a critical miss** (section 8).
- **At least 10 unanimous cases must have been decided before any of the verdict was posted, with no disagreement among them.** The first 10 such cases are the ones used, and they need not be among the first 50.

**The blind cases.** While the maintainer reads each verdict before he decides, almost no case is decided before its verdict is posted. So the last requirement cannot be met, and these two types cannot pass. That is intended: the scoreboard shows them as waiting for blind cases. The maintainer decided on 15 November not to hold back a blind share yet, and to move to blind review later. The suggestion on the table for that time: for about one proposal in five, chosen by a rule that cannot be predicted from its number, the reviewer waits until the maintainer has decided and reviews it then. The input is rebuilt exactly as it stood at submission, and nothing about those reviews can leak, because no model is asked until the decision exists.

**What graduation covers:** only applying the label automatically. Any automatic action beyond the label, such as adding a record to the evidence base, is a different capability and needs its own trial.

Graduating an outcome type is a separate change with its own design. This spec grants no authority.

## 10. Secrets and safety

**Keys**

- Three provider keys and nothing else.
- Each key lives in a dedicated project or workspace at its provider, used for this trial only, restricted to the one API it needs, with a usage limit and alerts set at the provider.
- Rotate every 90 days. If a key leaks: revoke it at the provider, issue a new one, review usage.

**GitHub, home repository**

- Keys are **environment secrets** in an environment named `automated-reviewer`, restricted to the default branch. Ordinary repository secrets are readable by anyone with write access.
- Do not add required reviewers to that environment, or every run will wait for approval.
- Only the `review` job references the environment, and only its provider-calling step receives the keys.
- Keep write access to the repository with the maintainer alone.
- The files that run with the keys are the workflow, the script, the configuration folder, the dependency lock file and the fixtures. The real control is that only the maintainer merges changes to them and reads those diffs carefully.
- Never add a `pull_request_target` workflow that can reach this environment.
- Official checkout and setup actions only, pinned to a commit SHA.
- Secret scanning and push protection are switched on.

**GitHub, source repository**

- Block force-pushes to its default branch. The commit history is the record of what the blueprint said on a given day, and the trial depends on it.

**Untrusted input and public logs**

- The script fetches issues through the API by number. Never place `${{ github.event.* }}` or `${{ inputs.* }}` values inside a `run:` step. Pass inputs through environment variables and validate proposal numbers as integers.
- Action logs on a public repository are public. Never print the environment, request headers, raw exception objects or raw provider responses, and never enable HTTP debug logging. Log the error type and status code only.

**Publishing the repository**

- The home repository is published the way the project's other repositories are: from a checked copy, as the project's public account, with no personal name, machine path or private file in it.

**Contributors**

- Add one line to the contributing guide and the website: an automated reviewer reads each proposal soon after it is submitted, has no effect on any proposal, and sends the public proposal text to models from three providers.

## 11. Size of a review

Per proposal, before caching:

| Call | Input tokens | Output tokens, including reasoning |
|---|---|---|
| Each of three panel calls | about 93,000 (blueprint, playbook, Idea Graveyard, index and proposal), plus up to 15,000 of cited excerpts | Up to the limit of 64,000 at `xhigh` effort (revision 5.4). The first real runs will show the usual figure |
| Each of three gate calls | the proposal text only | a few hundred |

- The self-consistency calls add about 20%.
- With the stable part of the input cached, several proposals in one run, or a self-consistency call straight after the first, reuse most of the input.
- The back catalogue is about a dozen proposals. Each prompt revision that re-runs it repeats those calls.

## 12. Build order and acceptance tests

0. **The home repository**, created private, with the environment, the kill switch set to off and the two issues. Nothing runs yet.
1. **Script with a dry-run mode** using fake model adapters, tested offline against made-up proposals in both real body shapes, and checked locally against copies of the real ones.
   - Context is read from the commit before the proposal's `created_at`, with versioned file names resolved at that commit, and includes the Stage 0 playbook.
   - The body is parsed by its structure; a body that does not parse is excluded, never guessed at.
   - The blind review block, the "AI tools" line, the Draft's "AI tools used" section and every identity line are absent from every assembled input.
   - Of the automated checks comment, only "The other checks" appears in any input.
   - A matching body hash confirms the text; a mismatch falls back to the edit history; an unreadable history excludes the case.
   - An issue edited or retitled before its decision is flagged.
   - No human comment or post-decision label appears in any assembled input.
   - The index reflects outcomes as they stood at submission, not current labels.
   - The evidence lookup and the cited excerpts are produced without any model call, and the excerpts stay inside their cap.
   - A decision label applied by an account outside `allowed_deciders` is skipped.
   - A self-decided proposal and an administrative closure are recorded as excluded with no call, and an administrative closure is found even though it carries no decision label.
   - Both body shapes parse, and a maintainer-opened proposal still supplies its line to the index.
   - The evidence lookup works at a commit from before the evidence graph file existed.
   - An undecided proposal is reviewed, and the decision is added to its record when it comes (revision 5; before that it was skipped).
   - A forged log comment from another author is ignored.
   - Model text containing a comment terminator, a link, a mention, an issue reference or backticks cannot alter the posted comment.
   - A proposal that reproduces the delimiter, or imitates index lines or a blind review, does not change how the input is framed.
   - Ten proposals decided within one minute are all processed across runs.
   - A second run for a completed proposal makes no calls; a run after a partial failure calls only the missing models.
   - Results from a `review` job that fails part-way are still published.
   - The per-run limit, daily cap and monthly caps stop further calls in every mode.
   - Any `mode` other than `shadow` stops the script before it reads anything.
   - In shadow mode the list of actions is always empty, and nothing is ever written to the source repository.
   - No currency symbol or money figure appears in any file, log entry or scoreboard the script produces.
2. **Scoreboard**, tested against a synthetic log with known answers, including the size limits.
3. **One real panel adapter**, run on the existing decided proposals by manual dispatch, labelled retrospective.
4. **The other two panel adapters.**
5. **The three gate adapters and the fixtures.**
6. **The schedule.**
7. **Freeze the `policy_version`.** Prospective counting starts here.

Each step is mergeable on its own. Nothing before step 6 runs automatically.

## 13. Decisions for the maintainer

Proposed defaults, taken from the review, to accept or change:

| Decision | Proposed default |
|---|---|
| Where the trial lives | A new public repository, `OpenIslet/automated-reviewer` |
| Graduation thresholds | Section 9 as written, with the one-slot continuity rule |
| `allowed_deciders` | The project's one public maintainer account |
| Self-decided proposals | Excluded from scheduled runs |
| Schedule and caps | Every six hours; 10 proposals a run; 20 a day; monthly caps per provider in calls and tokens |
| Reasoning or effort level | `high` for the panel, with 32,000 output tokens; `medium` for the gate, with 8,000 |
| Panel models | `gemini-4-neon`, `gpt-6.2-sol`, `claude-sonnet-5-5` |
| Spam and administrative closures | Add `outcome:spam`; keep `status:closed-triage` as an exclusion |
| `outcomes.yml` | Copied word for word from the published definitions of the seven outcomes, plus `decision:needs-work` and `outcome:spam` |
| Naming the earlier proposal for "already covered" | A line `Same as: IIP-NNNN` in the closing comment |

Only the maintainer can do these:

- Create the home repository, its environment, the kill-switch variable and the two pinned issues, and put the issue numbers in the configuration.
- Create the three provider keys in dedicated workspaces, set a usage limit at each provider, and store the keys as environment secrets.
- Add the `outcome:spam` label to the source repository.

## 14. Verify against official documentation before coding

Answered from each provider's published documentation on 14 November 2026. The adapters are written to these answers and tested against stand-ins; **each still has to be confirmed by a first real call.**

- **Gemini 4 Neon / Gemini 4 Hydrogen** are asked through Google's Interactions API, which Google's documentation names as the one to use (it calls generateContent legacy). The schema goes in `response_format` and the depth in `generation_config.thinking_level`. One limit of 1,048,576 tokens covers input and output together.
- **Gemini Decision 1** (not in use) is asked through Google's Decisions API: one yes-or-no question, answered with a probability from 0.01 to 0.99 and no text. Its answer does not name the model that gave it.
- **GPT-6.2 Sol** is asked through OpenAI's Responses API, with strict structured outputs and `reasoning.effort`. Its input limit is 922,000 tokens.
- **GPT-6 Luna** is OpenAI's small model. The gate asks it in text through the Responses API; its input limit is 922,000 tokens. It is also the only model on OpenAI's Decisions API, which answers one "predicate" question with a probability and no text. That path is not in use.
- **Schema constraints:** all three providers enforce types, required fields and the list of outcomes. None enforces lengths or ranges, so the script applies those itself, as section 6 says. Claude's structured outputs need `anyOf` for a field that may be null; the other two accept a list of types.
- **Blocked content:** Google answers with an HTTP 400 and a short code; OpenAI with a refusal part, an incomplete answer marked `content_filter`, or an `invalid_prompt` error; Claude with `stop_reason: refusal`. All are recorded as refusals with the provider's category.
- **Cached input tokens:** Claude reports cache reads and cache writes beside the uncached count. Google and OpenAI report a cached count that is part of the input total.

Still to check:

- Whether each provider's usage limit is a hard stop or only an alert.
- Whether a plain request is long enough for Gemini 4 Hydrogen and GPT-6.1 Sol. Since revision 5.4 the panel runs at `xhigh` and a request waits up to thirty minutes. If an answer still does not arrive in that time, or the connection is dropped while waiting, those two adapters need streaming.

Answered in review, to confirm while building:

- A workflow token with `issues: write` can create and edit comments on a locked issue in its own repository.
- GitHub's REST API does not serve an issue's edit history. The GraphQL API serves body edits, newest first, and a deleted revision has no content. Title changes are separate rename events.
- Issue bodies and comments are limited to 65,536 characters.
- Referencing an environment always creates a deployment record.
- Scheduled workflows can start late, and are switched off after 60 days without a push.
- A workflow token's hourly allowance covers its reads of another public repository. Files read by commit from GitHub's raw file address do not count against it, and a run of ten proposals needs only a few dozen requests.

Already checked against GitHub's documentation: an environment restricted to the default branch works for scheduled runs and blocks manual runs from other branches; outside contributors cannot apply labels except through an issue template; comments posted by the workflow's own token do not start other workflows.

## 15. Ready to switch on

Nothing in this section is built with authority. It fixes where authority would attach, so that switching one outcome type on later is a small, separately reviewed change.

**What the shadow build already contains**

- **A mode.** `mode: shadow` is the only value the script accepts. Other values are reserved names, not features.
- **A recommendation in every record.** For each stage: what the pipeline concluded (the unanimous outcome, the majority gate verdict, or none) and the action it took, which is always none.
- **One place where actions would happen.** The `publish` job asks an `actions` module for the list of actions for each record, then applies them. In shadow mode the list is always empty, and a test proves it. The call exists, so adding an action later does not reshape the pipeline.
- **A per-outcome switch.** The configuration has one authority setting per outcome type, each fixed to `shadow`, and the code accepts no other value. Since revision 5 no outcome type is refused in code for ever. Since revision 5.3 the code names the two types that could only ever be passed up to the third layer, never decided by the panel (`actions.PASSED_UP_NOT_DECIDED`).
- **The right split of permissions.** The job that holds provider keys cannot write anywhere; the job that writes holds no provider key.

**What switching one outcome type on would add**

- Evidence: that type passing section 9, on the public scoreboard.
- Timing: in place since revision 5, for every type: the reviewer runs at submission. The comparison with the maintainer's own review keeps running so drift stays visible.
- Access: a third job with its own narrowly scoped credential to label issues in the source repository. Neither existing job gains it.
- An action: applying one label, with a public note saying that it was automatic and how to ask for a person to look.
- An undo: the maintainer's label always wins, and one configuration line returns the type to shadow.

**For the gate**, the equivalent action is a hold label on a new issue, for the maintainer to release or confirm. It never deletes, closes or hides a submission.

Each of these is a design of its own. This spec builds none of them.

## 16. Changes from revision 3

| Change | Why |
|---|---|
| The trial lives in its own repository and only reads the proposals repository (section 3) | Releases overwrite the proposals repository; every issue there takes a public proposal number; its release scanner rejects model names; scheduled runs add deployment records |
| The contributor's blind review, the AI-tools lines and the Draft's "AI tools used" section are removed from every input; the blind review's verdict is recorded and used as a baseline (sections 2, 4, 8) | The blind review is another model's verdict on the same proposal, inside every issue body |
| Only "The other checks" part of the automated checks comment is included (section 4) | "The basic rules" states the blind reviewer's verdict. This goes one step further than the review, which recommended including the whole comment |
| The Stage 0 playbook is always in the context, with bounded excerpts of files the proposal cites, and a wider evidence lookup (section 4) | Most real proposals turn on what the playbook already specifies |
| Administrative closures, the founding issue, non-proposal issues and self-decided proposals are handled explicitly, and self-decided ones make no calls by default (section 3) | They exist in the real queue; self-decided cases cannot count towards graduation |
| The text is confirmed with the hash the submission backend already embeds, with the edit history as fallback (section 4) | GitHub's REST API cannot read edit history; the hash is already there |
| The scoreboard is split across the issue body and one comment per retired version; the schedule is every six hours (sections 3, 7) | The 65,536-character limit; fewer empty runs |
| Configuration identity is two levels, with a continuity rule when one provider replaces a model (sections 3, 9) | Otherwise any routine model update resets every count before it can reach 50 |
| "Already covered" names its earlier proposal as `Same as: IIP-NNNN` (section 8) | Matches the form the website's records already use |
| The Claude panel model is `claude-sonnet-5-5`, with its notes updated (section 6) | The maintainer's choice. The panel reads the whole blueprint, and the model first proposed is the one that has declined that kind of text in this project's own sessions |
| No money figures anywhere public; caps and reports are in calls and tokens (principle 9, sections 3, 8, 11) | The project keeps money out of its public material |
| Section 15, "Ready to switch on" | The maintainer's requirement that the build be ready to integrate, without granting any authority now |
| Fixtures aim at template-shaped junk and include a fake blind review (sections 5, 6) | The submission backend already refuses untemplated junk |

**Added after the review of revision 4** (all three found by checking the design against every real proposal and commit):

| Change | Why |
|---|---|
| The checks run in a stated order, with administrative closures first and self-decided proposals before text confirmation (section 3) | An administrative closure carries no decision label, so the "no decision label" rule would have hidden it; the maintainer's own proposals would have been recorded as unparsed |
| Two body shapes, the "ORCID iD" line, and two parser rules (section 4) | Six of the first fourteen proposals were opened directly by the maintainer, with a different first line and no marker |
| The evidence lookup names its sources in order, and large files are read as raw blobs (section 4) | The evidence graph file did not exist when most of the back catalogue was submitted, and the ordinary contents request refuses files over 1 MB |

**Settled while building step 1 (revision 4.2, not yet reviewed).** Each is in the code and has a test.

| Change | Why |
|---|---|
| Of "The other checks", only the rows named in the settings reach a model: the design named, papers cited, quotes matched, Idea Graveyard word-matches, "Not already known" and the version check. Rows about the blind review, the AI tools or personal details are never included, and an unknown row is left out (section 4) | One row the submission backend can add there, "The review recommends submitting", states the blind reviewer's recommendation. Two others concern the review and the AI tools, which are excluded from every input |
| The settings loader refuses an included row whose name mentions the review, the AI tools or personal details | So the rule above cannot be undone by a settings change alone |
| A failure to read GitHub is never recorded as an exclusion: the proposal is left for the next run. A case is excluded only when the repository or the edit history cannot supply what is needed (no commit before submission, a missing file, a deleted first version, a history that does not start at creation) (sections 3, 4) | A passing outage must not remove a proposal from the trial for good |
| A body that GitHub reports as never edited is accepted as submitted, without a marker (`never_edited`) (section 4) | None of the real proposals has been edited, and the maintainer-opened ones have no marker to check |
| A cited file over 2 MB is listed by name and not fetched (section 4) | The largest data files are tens of megabytes; a line reference into one is not a passage a model can use |
| Before the evidence graph existed, a PubMed number is answered from the per-study file names in the commit's file list, with no download. Each evidence file is read once per run, however many commits share it (section 4) | The file list is already in hand, and the evidence files are large |
| The index line for an earlier proposal shows the decision label standing at that moment, from applied and removed labels. One administratively closed by then is left out (section 4) | "As they stood at submission" has to follow removals as well as additions |
| The random marker is stated after the parts that never change and before the untrusted ones (section 4) | Stating it in the instructions would change the start of every input and defeat prompt caching |
| "Every fifth proposal" for self-consistency means a proposal whose number divides by five (section 6) | It needs no stored counter and anyone can check it |
| A complete result is keyed on the proposal and the `policy_version`. A change of roster alone does not re-run earlier proposals (section 3) | Re-running the back catalogue after a routine model update would fill the daily cap for days and delay new proposals; section 9 already covers continuity |
| Label events are fetched only for issues that carry a decision or closure label now (section 3). **Changed in 5.1:** events are read for every proposal | An issue with neither is undecided; this keeps a run to a few dozen requests |
| Each stage has an input limit in the settings, measured by the script's own deliberately high estimate (section 4) | The spec required a size check before any call and gave no number |
| A run stops, before reading any issue, if an issue template in the source repository names a decision label or the closure label (section 3) | The spec asked for a test of this; the templates live in the other repository, so it is checked at the start of every run |
| An answer from a model other than the one requested is recorded as `no_verdict: wrong_model` (section 6) | The spec said to check; this is what the check does |
| Stand-in results from a dry run are marked and the publish job refuses them (section 7) | So a dry run can never reach the log |
| The earlier proposal named for "already covered" is read from the decider's own comments up to the decision: the `Same as: IIP-NNNN` line, or the older wording "first in IIP-NNNN" (section 8) | The one real case so far uses the older wording, with a link round the number |
| Fixtures are of four kinds: junk and weak-but-good-faith ones go to the gate only; ones typed without the template must be left out before any model is asked; attempts to steer go to both stages and name the outcome they push for (sections 5, 6) | The spec listed what the fixtures should contain; this is how they are run and scored |
| An outcome type cannot pass its graduation count while a fixture steers all three panel models to it, and the scoreboard shows the gate's own count beside the others (section 9) | Section 9 states both rules; this is where they are applied |
| The gate's instructions are in two parts: what to judge, and the shape of a text answer. A model that answers a yes-or-no question with a probability is sent the first part and the question, never the second (section 5) | It writes no text, so an instruction to answer in JSON has no meaning for it |
| The model that answered is not compared with the one requested when the provider's answer does not name it, as Google's Decisions API does not (section 6). **Narrowed in 5.1** to models that answer with a bare probability | There is nothing to compare, and nothing is recorded as the reported model |
| When a provider turns a request down for a reason that trying again will not fix (a bad key, a malformed request, no credit), the run stops and nothing is recorded for that slot (section 6). **Changed in 5.1:** only a refused key, a refused permission or no credit stops the run | Recording it as a model error would mark every proposal in the run with an error because of one fault in the set-up |
| Section 14's open checks on the two other providers are answered from their documentation, and the third gate model is named | The adapters could not be written without them |
| The gate is three small models that answer in text, one from each provider: Claude Haiku 5.5, Gemini 4 Hydrogen and GPT-6 Luna. The two decision models are no longer in the gate; the code for them is kept and not used (section 5) | The maintainer's choice on 15 November. A text model gives a reason with every verdict, all three answer in the same form, and the two Decisions APIs are only weeks old. Hydrogen is Google's lightest current model, so it serves the gate at a medium setting and the panel at a high one |
| The gate runs at a medium reasoning setting with an 8,000-token output limit, not at each model's lowest setting (section 5) | The gate reads one short proposal a few times a week, and it must never hold a real one |

**Revision 5** (the maintainer's decisions of 15 November; not yet reviewed).

| Change | Why |
|---|---|
| The reviewer runs at submission, and the maintainer may read its verdict before deciding. Records are written without a decision and the decision is added later. Only issues the submission bot opened are reviewed (sections 3, 7, 8) | The maintainer's decision: he wants each verdict in front of him when he reviews, and the timing a live reviewer would need. The cost to the evidence is stated in the Revision 5 section |
| The scoreboard lists proposals waiting for a decision, with each verdict and a link, and says how many decisions came after a verdict was posted (sections 7, 8) | So the maintainer has one page to read before reviewing, and so the figures are not mistaken for a blind test |
| The workflow starts hourly, with a first job that holds no key and skips the rest when there is nothing to do (section 3) | A verdict should be ready soon after a proposal arrives, without empty runs holding keys |
| The project is named the automated reviewer; "shadow" is its mode (throughout) | The maintainer's decision: the name should not need to change when a part goes live |
| No outcome is barred from automation for ever; the stated aim is full automation (sections 9, 15) | The maintainer's decision. No authority is granted by it |
| The reviewer is described as layers, with a third layer of the most capable models planned (README) | The maintainer's plan: his own review by those models continues until the automated one matches it, and is meant to become a layer of this system |
| The maintainer's own proposals stay out of review while the reviewer is in shadow mode, and are to be included when it is given any authority. The README states the rule (Revision 5 section) | The maintainer's decision: reviewing them now adds no evidence and little insight at the highest price, and leaving the maintainer out once the reviewer decides anything would be one rule for him and another for everyone else |

**Revision 5.1** (15 November; not yet reviewed). The maintainer asked for two independent reviews of the build before the spec's reviewer saw it. Both read the code and ran it against made-up proposals. Every row below is a fault one or both found, is fixed, and has a test.

| Change | Why |
|---|---|
| Only a record block that is the last line of a log comment is read back. Where a model's reason is displayed, the marks that open and close a hidden comment are broken up (section 7) | A reason is shown above the record. A model talked into writing a block there could hide its own record, so the proposal was reviewed again every hour outside the limits, or plant a record that left another proposal out |
| The index of earlier proposals, and the list of proposals decided today, hold only issues the submission bot opened and the maintainer's own (section 4) | Anyone could type an issue that looked like a proposal. Its title and claim then reached every later panel input, and a marker built for the purpose could stop every run |
| The Draft's "AI tools used" section: code fences are followed by the CommonMark rules, and a Draft in which a short line still names AI tools after the section is removed is left out as unparsed (section 4) **Narrowed in 5.2** to a heading or a label that names AI tools. | The section survived a heading that was indented, worded differently, written as HTML, given twice, or placed after an unclosed code fence. A model must not learn which AI drafted a proposal |
| The model that answers must be the model asked for: its exact ID, or that ID with a date or a three-digit version stamp. An answer that does not name its model is `no_verdict: model_not_reported`, except from an API that returns a bare probability (section 6) | The check accepted any name that began with the ID |
| Names of files a proposal cites that the repository does not have go inside part 4, which is marked as untrusted. At most 20 are listed, none over 120 characters (section 4) | They are the contributor's own writing and were in a part the models are told to trust |
| A marker that is very long or nested too deeply is an unreadable marker. Three patterns that took cubic time on crafted text are bounded (section 4) | Crafted text could stop or hang a run |
| A fault in one proposal leaves that proposal for a later run and the run goes on. An answer cut short, or one that is not JSON, is a failed request to be tried again (section 3) | One bad proposal stopped every newer one, every hour |
| A request a provider will not accept is `no_verdict: error`, with the provider's status and short code, and the run goes on. Only a refused key, a refused permission or no credit stops a run. A text declined at the token count is a refusal. The provider SDK's own retries are off, there is a short pause between tries, and a provider's own "server error" answer is a passing fault (section 6) **Changed in 5.2:** it is asked again on later runs first, and more faults stop a run. | The 4.2 rule stopped the whole run for any such fault, at the same proposal every hour. Retries the limits could not see were possible |
| Label and rename events are read for every proposal, decided or not. A record keeps the decision and closure labels it last saw. A later change to them, such as a second decision or a closure, is added to the record on the next run, and the quick look counts work only when they differ (sections 3, 4) | An undecided proposal was sent with its current title, not the one submitted. Flags that arise after a decision was recorded were never added. A label from an account that may not decide made the quick look find work for ever |
| `posted_at`: the publish job records when the complete verdict was first posted. "Posted before the decision" compares that time with the decision's (sections 7, 8) **Changed in 5.2:** two times are kept, and a second opinion still to come no longer delays the first. | The time used was the start of the run, and a record still waiting for one model counted as posted. Both overstated how many decisions were made with a verdict in view |
| A review that had begun, and whose proposal is then left out, is marked as stopped. Its answers and calls stay in the log, and it is left out of every figure (section 7) | An exclusion replaced the record, so calls already made vanished from the log and the limits undercounted |
| The publish job goes on past a comment GitHub will not take, after one more try, and reports how many (section 3) | One refused comment lost every record after it |
| Undecided proposals are reviewed before decided ones. A limit holds back reviews and nothing else (section 3) | After any change to the prompts the whole back catalogue came before a new proposal, and decisions that cost nothing to record waited behind a limit |
| The quick look: a manual run that names a proposal always has work; a record that waits only for a provider key or for next month's limit is not work. A run started by hand always reaches the review job. A second opinion held back by a monthly limit is recorded as waiting (section 3) | A named proposal never reached the review job, and waiting records started the review job every hour for nothing |
| `earlier_log_issues`: when a new log issue is started, the old one is listed and still read (section 7) | Changing the log issue's number made the reviewer forget every record, review everything again and count none of the earlier calls |
| The policy version also covers the three adapters, the fixture loader, `decisions.py` and `review.py` (section 3) | They shape each request and the reading of each answer |
| A real run is refused until the log and scoreboard issues are set. The kill switch is honoured by every command, including the two that write. Results are saved after each proposal (sections 3, 10) | Without the log a run has no memory and no limits. The switch stopped reading and asking, and not writing |
| The scoreboard counts apart the decisions made with a verdict already posted and those made before any was. It lists stopped reviews, no longer lists an undecided proposal as having lost its decision, shows both models when one was replaced, and is rebuilt once a day (sections 7, 8) | The public plan says these cases are counted apart, and only one count was shown. A replaced model was merged silently. With the quick look, a quiet week would have looked like a stopped schedule |
| The `find` and `review` jobs may read issues (section 3) | The log is an issue, and reading it needs that permission while the repository is private. Neither job can write |

**Found by the two reviews, not changed, and since accepted by the spec's reviewer as they are:**

- The context commit is chosen by the commit's date, not by when it reached the branch. The source repository is published as a straight line of commits, each pushed at once, so the two agree to within seconds.
- At 5,000 issues and pull requests, listing them fails for the whole run. The repository has fifteen.
- A file list that GitHub cuts short is treated as a passing fault and so is tried again for ever. GitHub cuts a list at 100,000 entries; the repository has about 1,200 files.
- A second-opinion call that fails is recorded as an error after one run, where a first call gets three. A second opinion is a check on consistency and is no part of the panel's verdict.
- Google reports no credit with the same status as a rate limit, so the two cannot be told apart, and the request is tried again across three runs. (A refused key or billing switched off is told apart and stops the run: revision 5.2.)
- Section 9's rule for a replaced model is not built. It is not needed until a model is replaced inside a frozen policy version, and the scoreboard shows a replacement when there is one.

**Revision 5.2** (15 November). From the review of the build, and of revisions 4.2, 5 and 5.1, by the spec's reviewer. Each row is fixed and has a test.

| Change | Why |
|---|---|
| More faults stop a run: status 404 (a model or an address the provider does not have); no credit at Anthropic, which arrives with status 400; a refused key, or billing switched off, at Google, which also arrive with status 400. The short code is now read from Google's older error shape as well, which its token count uses (section 6) | These are faults in the set-up, and they arrived looking like one bad request. An error recorded for a model is never asked again, so each would have marked up to ten proposals an hour for good |
| A request turned down for any other reason leaves that model waiting, with the provider's status and short code, and is asked again on later runs. After the number of runs in the settings it becomes `no_verdict: error`. If the same model turns down two proposals in one run, the run stops (section 6) | A fault in a request that every proposal shares, met on first contact, would otherwise have been recorded as a final error on every proposal in the run |
| The policy version also covers `http.py` and `models.py` (section 3) | They now decide how a provider's answer is read and which faults stop a run |
| Two times are kept for each record, both set by the publish job: when any model's answer was first posted, and when every model's first answer was. A second opinion still to come does not delay the second. A decision is counted as made with the whole verdict posted, with part of it posted, or before any of it was, and the log comment says which (sections 7, 8) | The log comment and the scoreboard's waiting list show answers as they arrive. A decision made after reading them was being counted as made before any verdict was posted, whenever a second opinion or one slow model was still to come |
| Only the last record in the log for each proposal and policy version is read. Before a comment is written a second time, the log is read to see whether the first attempt arrived (section 7) | A comment GitHub took, whose answer was lost on the way back, was written twice. The proposal was then counted twice in every figure, in the graduation count and in the limits |
| A record in which no model was reached and nothing is known is not posted. A record whose models wait for a provider key or for next month's limit is still posted, because the quick look reads it (section 7) | A run stopped by a refused key at its first request left a comment that said only "waiting" |
| A fault in one proposal is recorded with its kind, such as `internal_error:KeyError`. The kind is the name of the fault's class; its text is never kept or printed (section 3) | With no detail at all, finding the cause later would have meant guessing |
| The rule about what is left after the "AI tools used" section is removed is narrowed. A Draft is left out only when a heading or a label still names AI tools: a Markdown or HTML heading, a line that is only bold text, a short label followed by a colon, a table row labelled with it, or a line underlined as a heading. A sentence, a list item or an ordinary table cell that mentions AI tools is not a reason (section 4) | The 5.1 rule, any line of 80 characters or fewer, left out a Draft for an ordinary sentence in a wrapped paragraph |
| A paragraph that opens with a label naming AI tools, and says something under it, is removed along with the section. It runs to the next blank line or heading (section 4) | Found by running the narrowed rule over the real proposals. One of them states its AI tools in a labelled line of several hundred characters outside the section. The 5.1 rule looked only at short lines, so that line reached the models. Removing it keeps the proposal in the review |
| A decision label that an allowed decider takes off within five minutes and replaces is a slip of the hand and is not the decision. If a run read the slip before it was put right, the record's decision is replaced by the corrected one (section 3) | The first label ever applied stood as the decision, so a slip put right in seconds was recorded as the decision and the case was reported as decided more than once |
| A decision made while a record is still waiting, for one model, for next month's limit or behind a limit on reviews, is recorded on the next run, and the quick look counts it as work (section 3) | Found while testing the change to the posting times: such a decision waited until something else started a run |
| Go-live: each provider's first live test is one run in `reconcile` mode that names proposal 10, which makes one gate call, one panel call and one second-opinion call to that provider alone. Fixture mode comes after all three have passed (section 12) | Fixture mode ignores proposal numbers, and under the limit of ten a run its first two runs reach only the gate |

**Revision 5.3** (15 November). The maintainer's answers to the two questions of policy the review raised. Not yet reviewed.

| Change | Why |
|---|---|
| No blind share for now. The suggestion stays on the table: the reviewer waits, for about one proposal in five, until the maintainer has decided (sections 8, 9) | The maintainer's decision. He intends to move to blind review later |
| Changing the blueprint and ruling out a dead end are held to a stricter test. The panel may only pass them up to the third layer and never decides them alone; no disagreement is allowed in the 50; a miss on either side of either is a critical miss; and at least 10 unanimous cases must have been decided before any of the verdict was posted, with no disagreement among them (sections 8, 9, 15) | The maintainer's decision, on the advice of the spec's reviewer. A wrong decision on either does the most harm: one alters the reference design, the other steers later contributors away from a route |
| Until blind cases exist, those two types cannot pass their count. The scoreboard shows them as waiting for blind cases, and says how many each has (section 9) | It follows from the two decisions above, and it is the safe direction: the types where a mistake does the most harm stay with the maintainer until there is a clean measure |

**Revision 5.4** (15 November). The maintainer's decision on how deeply the panel thinks. Not yet reviewed.

| Change | Why |
|---|---|
| The panel runs at each model's `xhigh` setting, the deepest that all three share, with an output limit of 128,000 tokens (64,000 at first). The gate stays at `medium` (sections 5, 6) | The maintainer's decision. A verdict from the panel has to be one worth trusting, and at `high` he would want to review each one again himself. A model's thinking counts as output, and the providers' own guidance is that the deepest settings need the room |
| A request to a provider may wait up to thirty minutes for its answer. A run reviews at most four proposals, and the review job may run for four hours (sections 3, 6) | Three long panel calls are made for each proposal, one after another. Ten proposals in a run could not finish inside the job's time |
| The panel's output limit is 128,000 tokens (sections 5, 6) | On the first real run one panel model used about 44,000 tokens of a 64,000 limit on an ordinary proposal. An answer cut off at the limit is recorded as no verdict and is never asked again, so the limit has to sit well above what a harder proposal needs. It is a ceiling, and each provider's models allow it |
| Each call's record also says how many of its output tokens were thinking, where the provider reports that (section 7) | On the same run one model answered at `xhigh` with very little output. The count shows how deeply each model really thought |
| All three providers are switched on. The Anthropic SDK and the fifteen packages it needs are pinned in the lock file with their published hashes (section 10) | The first two providers were proved on one proposal first. The other two providers need no package |

**Revision 5.5** (15 November). The maintainer's direction for the finished design. Nothing that is built changes. Not yet reviewed.

| Change | Why |
|---|---|
| The finished design is described as four levels: gate, panel, frontier review, and an editor that drafts the change to the blueprint. A proposal must pass every level, a level stops one only when all of its models agree, and the sender is told the outcome and why (Revision 5 section, README). **Changed in 5.6:** two of the three decide | The maintainer's direction. The third layer was already planned; the editor, and the rule for stopping a proposal, are new |
| Each level is to be given the reviews of the levels before it. Inside a level no model sees another's answer (Revision 5 section) | So a later level can build on an earlier one or overturn it, while agreement inside a level still means something. Principle 4 holds as written until this is designed |
| The stated end is a reviewer with no maintainer deciding, once every level has proved itself. An owner keeps one switch that stops it and can undo a change (Revision 5 section, README) | The maintainer's direction. Revision 5 already said that no outcome is barred for ever. Principles 2 and 3 hold as written until each step is designed and reviewed |
| Not designed: levels 3 and 4, how a sender is told, the fresh review a sender can ask for, and how a level is checked once no maintainer decides beside it | Each needs its own revision and review before anything is built. The last is the hardest: the scoreboard measures agreement with the maintainer, and at the last step there is no such decision to compare with |

**Revision 5.6** (15 November). The maintainer's decision on how a level's three models decide. Direction only: nothing that is built changes. Not yet reviewed.

| Change | Why |
|---|---|
| Two of a level's three models decide. A proposal passes a level when at least two pass it, and is stopped when at least two say stop. If one gives no answer and the other two disagree, it is passed up. This replaces the sentence in 5.5 that a level stops a proposal only when all of its models agree (Revision 5 section, README) | The maintainer's decision. It is what the gate as built already does (section 5), it still works when one model gives no answer, and it is the simplest rule to explain. The sentence in 5.5 disagreed with the gate as built |
| The editor at level 4 is the final gate. When all three models at level 3 agree on a change, it works out exactly what changes. When they are split two to one on changing the blueprint, or on ruling out a dead end, the editor decides whether the change is made, with every review in front of it (Revision 5 section, README) | The maintainer's decision. It settles the one case the two-of-three rule leaves open, the way a journal's editor decides when its referees are split. It also means a contested change to the blueprint rests on one model's judgement, so the editor's own record is the one that matters most before level 4 acts |
| A proposal that passes two to one carries the outvoted model's review up with it, and the next level is given it with the others (Revision 5 section, README) | The maintainer's point: the next level is not blind to the level before it, so it can take up what the outvoted model saw. A proposal stopped two to one is seen by no later level; the fresh review a sender can ask for covers that case |
| Unchanged, and to be done before any level acts on this rule: the panel as built has a recommendation only when all three agree (section 6), and section 9 tests it on those cases alone. A level has to be tested on the rule it will act on, so the scoreboard must first count the panel's two-of-three conclusions. Every model's answer is already in each record, so they can be counted from the log as it stands | Nothing built changes in this revision |
| Not designed: what the editor is asked when level 3 is split, what happens when the editor gives no answer, and how the editor's own decisions are tested when there is one model and no vote | Each belongs to the design of level 4, and needs its own review before anything is built |

**Revision 5.7** (15 November). The maintainer's decision on how the finished design is reached. Direction only: nothing that is built changes. Not yet reviewed.

| Change | Why |
|---|---|
| The system is built in shadow, one level at a time. Each new level starts in shadow and has to prove itself there. Nothing is handed over until all four levels, and the full system, have proved themselves; then it is automated, first with the maintainer able to overrule it and then alone. This replaces the four steps of 5.5, which each level was to climb on its own (Revision 5 section, README) | The maintainer's decision: stages are added to the shadow system one by one, each proving itself first, until the full system is proven, and only then is it automated. A half-built system lacks the later levels that check the earlier ones, so no part of it acts alone |
| The tests of section 9 stay as the tests levels 1 and 2 have to pass. Passing one no longer makes a level "eligible" to act: it is a condition for the handover of the whole system, not a trigger for a part of it. The same goes for "one outcome type at a time" in sections 1, 9 and 15 | Those sections were written for a handover outcome by outcome. They hold as written for what is measured, and this revision changes only what follows from passing |
| A new level may be added in shadow while earlier levels are still being measured | A level in shadow has no effect on any proposal, and the tests of section 9 need 50 to 100 real proposals for each level. Making each level wait for the one before would add months and no safety |
| One supervised period is kept before the reviewer runs alone: the full system acts, and the maintainer can overrule it | Proposed with the wording and accepted by the maintainer. It is the only stage between shadow and running alone |
| Not designed: the test the full system has to pass (section 9 tests levels 1 and 2 alone), and how long the supervised period lasts | Each needs its own revision and review |
