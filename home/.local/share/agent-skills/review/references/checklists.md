# Review checklists

Detailed checks for the passes in SKILL.md. Apply each check to the changed
code and to the callers and callees you loaded, not only to the lines in the
diff.

## Claims

- Every claim in the commit message holds in the code. Claims with "all",
  "only", "never", "always" or "exclusively" are verified by finding every use
  of the symbol and accounting for each hit, not by sampling the sites in the
  diff.
- Every new or changed comment matches the code, including the direction of
  conditions. A comment that says "if set" above code that checks "if not set"
  is a finding.
- A changed function signature, struct or enum has every caller, initializer
  and switch statement updated.
- A struct of callbacks has every callback that its users require.
- A new object is created the way the standard constructor creates it: the
  same flags, hooks, reference counts and registration. Diff the two.
- Arguments passed to other components are valid: sizes, format strings,
  non-empty names, units.
- Code changed in response to an earlier review gets the same scrutiny as new
  code. A suggested replacement helper often differs in locking, error
  handling or write semantics.

## Execution flow

- Every branch, switch case and loop condition, including the default case.
- Error paths restore everything the function changed before failing: struct
  fields, global state, counters, IDs set to a sentinel.
- Every `goto` cleanup label releases exactly what was acquired before the
  jump.
- Return values of calls that can fail are checked. Checking the result of
  allocation, initialization, locking and resource acquisition is part of the
  API contract, not defensive programming.
- Arithmetic on sizes and offsets: overflow, truncation by casts, sign changes,
  rounding that skips a capacity check.
- A NULL check comes before the dereference. Reading `p->field` dereferences
  `p`, not `field`.
- Loops run more than once. Pointers reassigned inside a loop free or hand off
  the previous value. Conditions that restart a loop or jump back with `goto`
  can loop forever. Exit conditions are the same on parallel paths, such as
  debug and non-debug, or success and error.
- Variables are initialized before every read. Writing to a variable, or
  passing it to a function that writes it first, counts as initialization.
- Macros and preprocessor conditions are spelled correctly and use the right
  prefix.

## Call stack

- Load every callee of a changed function in full, two to three levels down.
  A small change in a caller can break a callee that relied on the old
  arguments.
- Load every caller. Where a caller returns the value up, follow it up to
  three levels.
- Some bugs exist only for specific arguments. Check the arguments each caller
  actually passes.
- Globals, statics and macros are not found by following calls. Grep for every
  reader and writer.

## Side effects

For each behavior that the change modifies, write down the old behavior, the
new behavior and what must stay true. Then search:

- Locking changes: every function that touches the protected data. Does each
  one still hold the exclusion it needs before the access?
- New return values or error codes: every caller. Does each one handle the new
  value?
- Changed preconditions: every caller. Does each one still meet them?
- Added, removed or retyped struct fields: every use of the field.
- Changed lifetimes or reference counting: every caller of the allocation and
  free functions.
- Global state: every use of the variable.

Read every hit in full. A one-line grep match says nothing about ordering or
locking. Do not treat documentation of an invariant as proof that the code
upholds it. With many hits, start with the functions that touch the shared
resource, then the same component, then the rest.

## Resources

- Every allocation, file descriptor and reference is released or handed off on
  every path, especially error paths.
- No use after free, no double free. Using an object and then freeing it is
  fine. Freeing it and then using it is not.
- Not a leak: ownership moved to another object, the object was queued for
  later processing, or a callback frees it. Find where.
- Objects are fully initialized before they are published to a list, a table,
  another thread or user space.
- Reference counts are balanced. Nothing touches an object after its count
  drops to zero.
- Work handed to a timer, worker, thread, event source or callback is
  cancelled or unregistered before the memory it uses is freed. Teardown
  happens in the reverse order of setup.
- Several pointers to the same memory: a write through one changes what the
  others see.
- Memory freed through deferred release (RCU or similar) is removed from every
  lookup structure before the release is queued.
- A size that can be zero is handled.
- Long-running processes have bounds on memory, file descriptors, disk use,
  queue length, output size and retries. Temporary files and directories are
  removed on success, failure and timeout.

## Locking and concurrency

- Lock scope, not only lock presence: every access to the protected data
  happens between acquire and release. A check before taking the lock is not
  protection (time-of-check to time-of-use).
- Every lock is released on every error path.
- State is revalidated after a lock is dropped and taken again.
- Callers hold the locks that their callees require.
- Consistent lock order everywhere. Two locks taken in opposite orders on two
  paths can deadlock.
- No sleeping or blocking while holding a spinlock, inside an RCU read section
  or in another non-blocking context.
- No lock, database transaction or semaphore held across network I/O,
  subprocesses or other slow external calls.
- Lockless code uses the required barriers or atomic operations.
- A claim on shared work is one atomic conditional update, not a read followed
  by a separate write.
- A guard object bound to `_` or dropped right away releases the lock right
  away.
- Initialization flags are set only after the data they announce is ready.

## Security

- Untrusted input: network data, files, user arguments, environment, data from
  another process or privilege level. Follow it to every place where it
  selects a size, an index, a path, a command or a format.
- Paths built from untrusted strings stay inside their directory: no `..`, no
  absolute paths, no symlink escapes.
- Command arguments built from untrusted strings cannot become options: pass
  argv directly and separate options with `--`.
- No uninitialized memory or secrets copied to a less privileged reader, logs
  or error messages.
- Severity rises only when untrusted input can cross a privilege boundary,
  cause denial of service or corrupt unrelated state. Data passed to a reader
  that already has equal privileges is not a leak.

## Error handling

- Code that handles untrusted or runtime data returns errors instead of
  aborting. An abort or panic on untrusted input is a denial of service.
- Ignored errors: a failed state update can leave work stuck, and a failed
  cleanup can leak. A default value on parse failure turns "failed" into
  "empty".
- Intentionally ignored results are marked (`(void)` in C, a comment
  elsewhere).
- Temporary errors are retried with backoff and a limit. Permanent errors fail
  right away.
- An error caused by user input (a bad option, a missing file, an invalid
  configuration value) produces a message that says what is wrong and what to
  do about it, not a traceback or a bare error code.

## Tests

- New behavior has tests, if the project expects them.
- Leaks and aborts in test code are fine. Shared fixed ports, shared files and
  global state that make concurrent tests interfere are not.
