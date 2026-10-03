# ADR-002: Keep maintenance alive while forwarding is paused

PR #63 adds periodic authorization checks and download-cache cleanup to the
existing scheduler. Pausing that scheduler also freezes account status and leaves
interrupted downloads indefinitely, although the user action only promises to
pause fetching and sending.

Keep one scheduler and use the existing `Forwarder._stopping` gate and
`request_stop()` cancellation for both Web and command pauses. Maintenance jobs
continue running; plugin termination still pauses and shuts down the entire
scheduler. This avoids a second scheduler and its startup/shutdown ownership
while retaining the existing forwarding intervals on resume.

The download cache is exclusively the real `telegram_download` directory under
the resolved plugin data directory. Reject symbolic links, Windows reparse points
(including junctions), and paths resolving elsewhere before downloads or cleanup.
Redirecting this cache is intentionally unsupported because TTL cleanup deletes
its contents; linking the whole plugin data directory remains supported.

Dashboard polling uses one timer whose delay follows active work (2 seconds) or
authorized idle state (30 seconds). Replace the timer only when that delay changes,
so repeated synchronization cannot keep postponing an unchanged deadline.
