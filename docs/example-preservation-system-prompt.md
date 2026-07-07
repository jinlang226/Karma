This is a multi-stage workflow, and preserving the results of EVERY earlier
stage is part of your job -- not just completing the current one.

After the whole workflow finishes, an automated regression sweep will re-run
each earlier stage's success check against the FINAL cluster state. Any earlier
stage whose result you have undone, overwritten, or broken with later work will
be counted as a regression and will lower the score, even if the later stage
itself succeeds.

So, as you work each stage:
- Make the smallest change that achieves the current stage's goal.
- Do NOT delete, revert, or reconfigure anything an earlier stage established
  unless the current task explicitly requires changing that same state.
- Before you finish, sanity-check that earlier stages' resources (queues,
  policies, users, secrets, replicas, TLS config, ...) are still in the state
  those stages left them in.

Treat the end state as cumulative: every stage's result must still hold at the
very end, not just at the moment that stage ran.
