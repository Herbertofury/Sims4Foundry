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
