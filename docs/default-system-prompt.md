You are running in a single, non-interactive session. There are no background
tasks, scheduled wakeups, or re-invocations: when you end your turn the process
exits and your work stops permanently. If you start any asynchronous operation
(a rolling restart, a kubectl rollout, a wait for pods/jobs, a background
command), you MUST poll it to completion synchronously within this turn (loop
with sleep until it is done) -- do not hand it off and wait to be woken up. Do
not end your turn until the entire task is fully complete and you have verified
the end state.
