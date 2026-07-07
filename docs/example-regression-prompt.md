You are auditing a multi-stage Kubernetes benchmark run. Each stage asked an
agent to perform a task; an automated oracle then checked the result. Every
stage passed its oracle when it ran. After the whole workflow finished, KARMA
re-ran each passed stage's oracle once more (a "regression sweep") to see
whether the agent's later actions broke an earlier stage's success.

Stage "$stage_id" PASSED when it ran, but its oracle now FAILS on re-run.
Decide whether this is a REAL REGRESSION (the agent carelessly broke this
stage's result with later actions) or a FALSE POSITIVE (the failure is
expected -- a LATER stage was legitimately supposed to change the same state,
so the stale re-check no longer applies).

## Oracle re-run output for $stage_id (now failing)
$regression_output

## Every stage's task, in execution order (a later stage may legitimately
## change the state this stale oracle checks)
$stage_prompts

Respond with ONLY a JSON object on one line:
{"legitimate_regression": true|false, "reasoning": "<one or two sentences>"}
- legitimate_regression=true  => the agent really broke this stage (counts against the score)
- legitimate_regression=false => false positive; a later stage legitimately changed this state
