---
name: ace
description: Add a feature to an existing Python repo test-first with three independent models (tests, code, review) using the `ace` CLI. Use when the user wants a feature built with ace, or asks to run ace.
---

# ace

`ace` turns a spec into a patch: one model writes tests from the spec, a second implements until
they pass (it can't change them, and some are hidden from it), and a third reviews the diff
against the spec. Each model works from the spec and the codebase, never from another model's
work, so every decision must be written in the spec. Your job is to get that spec right with the
user, then run `ace` and report.

## 1. Understand the request and the codebase

Read enough of the repo to know where the feature would fit and how similar existing features
behave (errors, naming, return types, whether they copy or modify inputs).

## 2. Draft the spec

Write what the feature does, in plain sentences, plus concrete examples with exact inputs and
outputs. Describe behavior only; don't name files or internal functions. Do name the public
surface the user will call if it matters to them (a command, route, or function name).

## 3. Close the gaps with the user

A gap is any place where two reasonable implementations would behave differently. Look for them
in: empty and invalid input, errors raised, ordering, whether inputs are modified, whether the
result shares data with the inputs, how it interacts with existing features, and how it is
called.

For each gap, ask the user a concrete question with a small example and a suggested answer that
matches how the codebase already behaves. For example: "`merge` shares lists with its inputs.
Should the new function do the same? (a) share, like `merge` (b) copy". Ask a few at a time,
using a multiple-choice question tool if you have one. Skip anything that wouldn't change
behavior. Repeat until no gaps remain, writing each answer into the spec.

## 4. Lock it in

Show the user the final spec and get an explicit yes. Save it as `specs/<feature>.md` in the
project. From here on, don't edit it; if something changes, go back to step 3.

## 5. Run

From the project root, with the repo's current work committed (ace starts from the last commit):

```bash
ace run specs/<feature>.md --repo .
```

The gateway settings and the three model names come from `ace login` and `ace config`. If
they're missing, ask the user to run those themselves, so the API key never passes through this
chat; don't guess model names. A run takes several minutes, and more on a large test suite. Run
it in the background if your harness has a short command timeout.

## 6. Report back

Read `~/.ace/runs/<feature>-<timestamp>/result.json` and tell the user:

- PASS or FAIL, and the step it stopped at (`stopped_at`)
- The path to `changes.patch`; they apply it with `git apply <path>` from the repo root
- Any `heldout_failed` or `regressions`, and anything in `reverted` (a sign the implementer tried
  to change tests or test settings)
- The reviewer's `summary` and `issues`

If hidden tests failed, there is no review: show the user those tests and the code they exercise,
and let them decide which is wrong. If the issues point to a gap in the spec, go back to step 3
with the user. Don't apply or commit the patch unless the user asks.
