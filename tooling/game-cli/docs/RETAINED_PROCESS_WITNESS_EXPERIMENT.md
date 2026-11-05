# Retained-process witness source checkpoint

This experimental injected-API registry retains a query-only handle before issuing a same-owner witness, and refuses to repeat uncertain acquisition or release. A provider's close acknowledgement is not proof of OS release. It is not wired to the game CLI or qualified for game use.

The witness is in-memory, unauthenticated and cannot survive provider termination. An acquire-then-throw can lose the raw handle; executable path hashing is not proof of the loaded image; lifetime slots lack a qualified retirement path; and window/input dispatch is not atomic. Do not use it as a shared registry or as proof of save or normal exit.

Twelve fixture tests passed, including one Windows test against a newly spawned owned child. Its cleanup can still fail or become uncertain; no descendant-reaping guarantee is made. No Sims process or profile was used. Further provider, authority and game qualification is required.
