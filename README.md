# OpenIslet automated reviewer

OpenIslet is an open project that keeps a public blueprint for a cure for type 1 diabetes. Anyone can send it an improvement proposal, and the project's maintainer decides what happens to each one.

This repository holds an automated reviewer for those proposals. **Today it runs in shadow mode: it reviews every proposal and decides nothing.**

## The plan

The aim is for proposals to be reviewed automatically, with every kind of outcome handled by the reviewer in the end. The project is getting there in steps, and in the open.

1. **Now: shadow mode.** The reviewer reads each proposal soon after it is submitted and records what it would decide. The maintainer still reviews every proposal himself, with his own review by the most capable AI models, and his decision is the only one that counts.
2. **Next: compare.** Every verdict is kept beside the maintainer's decision, and a public scoreboard counts how often they match.
3. **Later: one kind of outcome at a time.** When the reviewer has matched the maintainer on one kind of outcome over a stated number of cases, that kind can be handed over. Each handover is a separate, published change.

Two kinds of outcome are held to a stricter test, because a wrong decision on either does the most harm: changing the blueprint, and ruling out a dead end, which adds to the Idea Graveyard. The panel never decides these alone. The most it can do is pass a proposal up to the third layer, and only after it has matched the maintainer with no disagreement at all, including on cases he decided before he could read its verdict.

The reviewer works in layers:

| Layer | What it does | Models |
|---|---|---|
| 1. Gate | Is this a good-faith proposal, or spam? | Three small models, one from each of three providers |
| 2. Panel | Which outcome should this proposal get? | Three stronger models, one from each provider |
| 3. Frontier review | A deeper review by the most capable models | Planned, not built |

## What it does today

Soon after a proposal is submitted, the reviewer:

1. asks the three gate models whether it is spam;
2. asks the three panel models which outcome they would give it;
3. writes down what each model said, with its reasons;
4. adds the maintainer's decision beside it when that decision is made;
5. keeps a scoreboard of how often the two were the same.

The maintainer can read the reviewer's verdict before he decides. The scoreboard says so, and counts the decisions he made with a verdict already posted apart from the ones he made before any verdict was. The plan is to move to blind review later, where the reviewer's verdict is not seen until after the decision.

## The maintainer's own proposals

The maintainer sends proposals too. In shadow mode the reviewer does not review them. He both writes and decides them, so they cannot test the reviewer, and they already go through his own review by the most capable AI models. Each one is listed in the log as left out, with that reason.

When the reviewer is given authority over any kind of outcome, the maintainer's proposals will go through it like everyone else's.

## It decides nothing

In shadow mode the reviewer never labels, closes, hides, blocks or comments on a proposal, and it writes nothing to the repository where proposals live. It reads public proposals and writes its results here.

The code is built so that this cannot change by accident:

- The only setting it accepts is `mode: shadow`. Any other value stops it before it reads anything.
- The list of actions for every result is always empty, and a test proves it.
- The job that can ask a model can write nowhere. The job that can write holds no model key.

## What a model is shown

A model sees what was submitted, and nothing that came after:

- the proposal's own text and title, as they were sent, and the version of the blueprint it says it was written against;
- the project's files as they stood just before the proposal was sent;
- passages from project files the proposal cites;
- a list of the studies it names, saying whether the project already held each one;
- earlier proposals, with the outcome each had at that time;
- part of the report from the checks that software ran when the proposal was sent: only the rows about the proposal itself.

A model is never shown who sent the proposal, which AI tools the contributor said they used, the contributor's own blind review or anything the checks say about it, any comment written by a person, or the maintainer's decision.

Text written by a contributor, or taken from it, is wrapped in markers, and the models are told it is material to assess, never instructions to follow.

## Where things are

| Path | What it is |
|---|---|
| `docs/SPEC.md` | The full design, and the reason for each part of it |
| `settings/` | The settings, the outcome definitions and the instructions given to the models |
| `reviewer/` | The code |
| `scripts/automated_reviewer.py` | The entry point for the jobs |
| `.github/workflows/automated-reviewer.yml` | The workflow that runs them |
| `tests/` | The tests, and the made-up test submissions in `tests/fixtures/` |

## Running the tests

The code needs Python 3.9 or later and PyYAML.

```bash
python3 -m pytest -q
```

Every test uses made-up proposals and a stand-in for GitHub. No test calls a model or the network.

## Sending a proposal

Proposals are not sent here. See the project's website, [openislet.org](https://openislet.org), for how to send one. In shadow mode the reviewer has no effect on what happens to it.

## Licence

The code and its documentation are under the Apache License 2.0. See `LICENSE`.
