# Verifying findings and dismissals

Reviews fail in two ways. They report bugs that do not exist, and they find a
real bug and then argue themselves out of it. The second happens more often
than expected, so dismissals need as much proof as findings.

Verify each contested finding on its own, highest severity first. Checking many
findings in one go tends to drop the weaker ones and blur their details.

## Before you verify

- Reread the full commit message. If it explains the behavior in question,
  check that explanation against the code.
- For each finding, write down the exact question that decides it ("Does every
  caller of foo() hold bar_lock?"), then read the code that answers it.

## Proof checklist

Answer each question in writing before reporting a finding.

1. Can the path execute? Give the call chain from an entry point to the
   defect. List the conditions on the way and show that they can all be true
   at the same time. Confirm that the code is not dead or disabled.
2. Is the failure concrete? Name the crash, hang, corruption, leak or wrong
   result, not "increases risk".
3. Did you check the context? Callers two to three levels up, the
   initialization and cleanup paths, the conventions of the project.
4. Is it wrong? Check whether the commit message or a comment shows that the
   author considered the case. Check for a documented limitation. Test code is
   held to a lower standard.
5. Does the bug exist today? "Theoretical" means that no path reaches the code
   today. A bug that depends on timing or system state is real, with a
   conditional trigger.
6. Is the code as you think it is? Reread the file at the reviewed revision and
   quote the lines exactly. Check arithmetic. Confirm that every function and
   variable you name exists.
7. Is it still there at the end of the series? Search forward in the series
   only, never backward in history.
8. If a comment or documentation is part of the argument, did you read the
   implementation? Comments get copied between implementations and between
   branches of `#ifdef`. A bug dismissed because of a wrong comment is worse
   than a false report.

Then argue as the author. Write down the strongest argument against the
finding: a lock held by a caller, an ownership transfer, a cleanup callback, an
invariant set up elsewhere, intent stated in the commit message. These are
hypotheses to check, not conclusions. Refute each one with code, or drop the
finding.

Final filter. Keep the finding only if all four answers are yes:

- Do I have proof, not just suspicion?
- Would an expert in this code agree?
- Is it worth the maintainer's time?
- Is it a concrete bug, not a wish for defensive programming?

## Valid dismissals

Quote the code that makes the bug impossible: the guard, the lock, the cleanup
path or the caller. Examples:

- Every caller, in every mode, holds the lock or validates the value. List the
  callers.
- Setup finishes before the object is published and teardown starts after it
  is unpublished, so the two cannot race.
- Ownership moves to another object, a queue or a callback that releases it.
- The value is used before it is freed, not after.
- A writing function initializes the variable before anything reads it.
- The language, the compiler or a documented API guarantee rules it out.
  Callers may rely on documented guarantees without checking them again.

## Invalid dismissals

- One caller or the happy path, when other callers or modes exist: error paths,
  resume, reset, reload, debug interfaces, other build configurations.
- "The caller handles it" or "the API layer validates this", without reading
  the caller or the API layer.
- "Unlikely in practice", "only if X fails" or "the old code was worse" for a
  crash, hang or corruption. Only a structurally impossible trigger counts.
- A comment or documentation, without reading the implementation.
- "Harmless" or "intentional" for an unpaired setup call, a cleared error that
  later code would have read, or a state change that should not happen, without
  code that shows that nothing depends on it.
- "The build or an assertion would catch it." A build failure in a valid
  configuration is a bug of its own.
- A check that holds in one configuration only.
- A race dismissed because the code detects the bad state and aborts, unless
  every instruction between the start of the race and that check is safe on the
  bad state. List them.
- Infrastructure with no callers yet. Search forward in the series for the
  commit that uses it. Without a series, report the bug and note that a later
  change might enable it.

## Retractions

If you concluded that something is a bug and later reversed yourself, say so,
recheck the reasoning behind the reversal and require more proof than usual.
Before reporting, reread your list of ruled-out issues against the invalid
dismissals above, and ask whether focusing on them made you miss other
problems.

## Patterns that are rarely bugs

- A changed order of operations, unless it creates a race, breaks a dependency,
  allows a deadlock or leaves state invalid. Show which.
- Removed assertions or warnings, unless they were the only runtime check.
- A slower path that the commit message explains as a trade-off.
- Leaks and aborts in test programs, unless they hang or crash the system.
- In a revert, the bug that the reverted commit fixed. Look only for new
  problems.
- A defect in one commit of a series that only leaves work incomplete, when a
  later commit completes it.

## Races

To report a race, name the data, the lock that should protect it, and two code
paths that can run at the same time. Quote both. Show the order of events as
two columns:

    thread A                        thread B
    obj = lookup(id);
                                    remove(id);
                                    free(obj);
    obj->count++;   /* use after free */

For a use after free caused by asynchronous work, count every reference taken
and dropped on both sides, and show which side lacks the reference that would
keep the object alive.

## Project rules

When a finding rests on a documented project rule, check only three things:
the rule exists, the cited code exists, and the code breaks the rule. If all
three hold, report it. Do not argue reachability or severity away. If your own
analysis disagrees with the rule, report the finding as a concern and give both
the rule and your reasoning.
