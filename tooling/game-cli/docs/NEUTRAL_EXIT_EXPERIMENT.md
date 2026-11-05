# Independent normal-exit source checkpoint

This module is an injected-provider orchestration checkpoint, not an enabled CLI command or a game-qualified exit. It retains separate save-and-exit and discard-and-exit intents, records a one-shot intent before dispatch, and reconciles only the original request. Synthetic fixtures exercise interrupted input, identity changes and uncertain lease acquisition or release.

No concrete game input/readback provider, authenticated shared registry, atomic UI-to-dispatch binding or recoverable native process-lease witness is supplied. A provider assertion is not proof of OS handle release, normal shutdown or save integrity. Do not use this module against a game until those boundaries are implemented and independently qualified. Do not replay an uncertain confirmation.

The source was tested using 25 isolated injected-provider fixtures against the pinned Foundry 9761ff51b949689d2c7845dc27908c300cdb33f0 dependencies; those fixtures did not access a game or profile. Broad repository discovery, actual gameplay and native process handling remain unverified.
