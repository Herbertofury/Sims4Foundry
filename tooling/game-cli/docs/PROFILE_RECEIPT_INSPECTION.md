# Restored-profile receipt inspection

`foundry-game test-profile inspect-restoration-receipt --session ...` checks a previously recorded restoration receipt and its expected directory identities. It does not move a profile or authorize launch, and it does not verify save integrity. Inspection uses the existing lifecycle lock, which may create or initialize a sibling lock file.

The result is a point-in-time observation. Cooperative locking does not exclude unrelated writers or prove atomic filesystem state; directory replacement, ABA and changes after observation remain possible. Existing path-based rename/recovery operations are unchanged and retain their separate unresolved race. Do not use this result as permission to launch, restore or replay an uncertain operation.

Eight focused synthetic tests passed against staged package code and owned temporary records, with the Windows admission guard stubbed. They are not a real-profile, game, rename-race or save test.
