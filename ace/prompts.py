TEST_WRITER = """\
Write tests for a feature that does not exist yet in this repository. Another agent will then
implement it, without being able to change your tests.

<spec>
{spec}
</spec>

1. Read the repository to learn its layout, conventions, and test style.
2. Decide where the feature belongs and what its public interface is (import path, names,
   signatures). Your tests define that interface, so choose what a maintainer here would.
3. Create new test_*.py files in the repository's test directory.

Rules:
- Only create new test files: no edits to existing files, no conftest.py, no feature code.
- One behavior per test. Cover every requirement and example in the spec, plus the edge cases
  and errors it implies.
- Test only what the spec requires. Where it leaves a detail open (exact wording, other ways to
  call the feature), don't pin it down.
- State expected results directly, as exact values or as rules that must always hold. Never
  compute them with your own version of the feature: a test that shares the code's logic shares
  its bugs.
- {hypothesis}
- Test only the public interface.

Finish by listing your files and the interface they expect.
"""

HYPOTHESIS_YES = "Check rules that must hold for any input with Hypothesis (`from hypothesis import given, strategies as st`)."
HYPOTHESIS_NO = "Hypothesis isn't installed; use plain pytest."

TEST_RETRY = """\
Your tests were rejected:

{problem}

Fix them under the same rules.
"""

IMPLEMENTER = """\
Implement the feature below in this repository. Another agent already wrote tests for it from
the spec.

<spec>
{spec}
</spec>

Tests: {tests}
They show the expected interface. Run them with: {pytest}

Rules:
- Follow this codebase's conventions. Create new modules if the tests import ones that don't exist.
- Don't change tests, conftest.py, or pytest settings; such changes are reverted.
- Solve the general problem, not the test inputs: more tests are hidden and run afterward.
- The existing test suite must still pass.

Finish by summarizing your changes.
"""

IMPL_RETRY = """\
The tests failed.
{reverted}
{output}

Fix the implementation, not the tests.
"""

REVIEWER = """\
Review a change against the spec below. One agent wrote the tests first, from the spec alone;
another then wrote the code to pass them. Passing tests are not proof that the spec is met.

<spec>
{spec}
</spec>

<diff>
{diff}
</diff>

Check:
- Does the code meet every requirement? What is missing or wrong?
- Do the tests check the spec correctly and completely?
- Does the code fit this codebase (placement, naming, clarity)?
- Does the code cheat, for example by special-casing test inputs?

Reply with only this JSON:
{{"pass": bool, "cheating": bool, "issues": [str], "summary": "2-4 sentences"}}
Judge only what the spec states. "pass" means the code meets the spec, the tests check it
correctly, and the spec leaves nothing open that changes behavior; otherwise it is false and each
problem is in "issues". "cheating" means the code games the tests.
"""
