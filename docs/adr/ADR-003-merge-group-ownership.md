# ADR-003: Keep waiting consistent with merge group ownership

`MessageMerger` uses the same ordered group selection for merging and waiting.
The first rule that can form a complete group owns its messages. An incomplete
rule cannot delay messages that already belong to that group, including a
trigger nested inside it. The waiting pass excludes messages reserved by other
groups when it counts missing follow-up messages.

A pending trigger can occur between a preview and its non-adjacent original.
Move the waiting boundary back to the start of any complete group it would
split. `Forwarder` advances `last_id` from the queued prefix, so splitting that
group could lose its association or advance past a message still waiting.
Recompute ownership for each pass instead of caching a plan whose freshness
would depend on message markers and queue state.

`tests/test_merger_rule_ownership.py` covers nested triggers, competing counts,
non-adjacent associations and reserved messages using synthetic rules.
