# Cross-workspace lifetime reservation experiment

`global_lifetime_authority` is an unconnected source-level experiment. It
uses Windows' OS-resolved LocalAppData location, rather than a workspace path
or caller-provided environment variable, to identify one cooperative authority
for the current user. It publishes an exclusive reservation keyed by PID and
creation time before any process acquisition. The reservation also binds the
selected target digest, original session, witness request and journal digest.
An existing or uncertain reservation must be inspected; it is never replaced
or automatically cleared. This module never opens a process or accesses a game
profile.

The fixed location and exclusive file publication coordinate only callers
which use the same module and OS account. They do not authenticate same-user
programs, prevent deletion or rollback, exclude path replacement races, prove
power-loss durability on Windows, or prove that a running image matches a
file. The authority's initialization and reservation can be incomplete after
interruption or a lost acknowledgment. Do not recreate an uncertain
authority or replay an uncertain claim. There is no qualified retirement or
restart-recovery procedure.
An initialized authority or a missing/inaccessible reservation does not prove
that an earlier independent authority sent no input. The lower-level
reservation API accepts caller-supplied binding data; `claim_session` first
reopens an existing unused session and binds its original journal and registry.
`claim_bound` instead returns a non-serializable in-process ticket tied to the
exact registry, journal and witness objects before witness acquisition.
The witness's original owner thread excludes reentrant acquisition and recovery
while that in-process claim is being published. The claim records the witness
acquisition attempt in its registry before publishing the global reservation.
That attempt uses a distinct `global_acquire` operation, so reconstructing a
fresh witness wrapper over the same provider cannot resolve it as an ordinary
acquisition or bypass an uncertain attachment.
`acquire_bound` makes one provider acquisition, checks the original retained
handle, publishes the attachment, and then resolves that same registry attempt
while holding the original journal and registry locks. A failure can leave an
unresolved attempt or attachment; do not retry the acquisition. A registry now
refuses replacement acquisition after any original acquisition attempt, while
remaining able to read older histories with multiple acquisitions. Recovery of
non-global witnesses uses the original in-memory provider; uncertain global
attachment remains fenced.

This is not authentication against arbitrary same-user or in-process Python.
The journal and cooperating registry locks exclude another witness using that
same registry during claim and attachment. Other authorities, non-cooperating
writers and path replacement are not excluded. An existing attachment can be
inspected after a lost reply; this does not prove the witness is still held or the gate is free.
The registry, journal and global reservation are not an atomic snapshot.
Neither the attachment nor the claim reconciles unknown prior authorities.

Owned synthetic-directory tests cover two concurrent callers, two separate
Python processes, changed target identity, a lost claim reply and malformed
records. A read-only check resolved the actual Windows LocalAppData known
folder. No real authority was initialized, and no game or profile was touched.
The installed CLI exposes `foundry-game global-authority initialize`,
`foundry-game global-authority status`, and `foundry-game global-authority
inspect --identity ORIGINAL_IDENTITY.json`. Initialization writes to the
OS-resolved per-user authority location; it does not reserve a process or open
the game. Do not repeat initialization after an uncertain result. The CLI
does not expose reservation creation as a standalone action. This experiment
is not connected to `experimental-live-owner`; live admission remains denied
pending independent review and a complete shared authority lifecycle.

`foundry-game global-authority inspect-prior --identity ORIGINAL_IDENTITY.json
--sessions SESSION_LIST.json` inspects up to 64 explicitly supplied older
authority/session pairs. The session list is a JSON array of objects with
`authority` and `session` path fields. It reports pending registry attempts
and nonterminal journal runs for the specified lifetime. It cannot discover
unknown authorities, prove that the list is complete, verify game state or
authorize live input. Cooperative lock files or SQLite sidecars may be created;
observations across sessions are not one atomic snapshot. A resolved witness
acquisition is reported as possibly still held; inspection does not contact the
original owner or prove release.
