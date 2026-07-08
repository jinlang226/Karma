You are running in a single, non-interactive session. There are no background
tasks, scheduled wakeups, or re-invocations: when you end your turn the process
exits and your work stops permanently. If you start any asynchronous operation
(a rolling restart, a kubectl rollout, a wait for pods/jobs, a background
command), you MUST wait for it to finish within this turn -- prefer a single
blocking command (`kubectl rollout status`, `kubectl wait --for=...`) over a
manual sleep-and-poll loop, and only fall back to looping with sleep if no
blocking wait exists. Do not hand it off and wait to be woken up. Do not end
your turn until the entire task is fully complete and you have verified the end
state.
