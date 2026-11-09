# Evidence and authority

Before build, define proof obligations per acceptance row, not merely a test count.
At acceptance, record exact candidate head, contract digest/revision, environment, provider mode, configuration/feature-flags digest, actual code/test references, journeys, oracle, observations, actor, findings, limitations and hashed retained artifacts.
The interface stores append-only result attempts; failed and superseded observations remain visible.
The latest attempt for the exact acceptance row, approved digest and candidate is authoritative, not an older passing attempt.
Changing a candidate invalidates evidence until reverified.

A required path must be implemented, reachable and enabled in its promised environment.
A placeholder, disabled flag, fake screenshot, fixture interception, hard-coded success, in-memory substitute for required persistence or synthetic-only proof cannot meet a required real path.
Use real UI entry and normal permissions for browser obligations, with storage/network/provider correlation when those are part of the oracle.
API/CLI-only products need an actual API/CLI journey, not irrelevant browser ceremony.
Sandbox and real production proof are distinct and never interchangeable unless the approved policy explicitly permits that mode.
Missing browsers, credentials, authority or budget leave the exact criteria unverified.

External operation budgets are explicit allowlists with provider, operation, tenant, credential reference, request/money ceilings, currency, expiry, permitted data, idempotency and cleanup.
No budget means zero external authority.
Reserve capacity atomically before the operation, including retries and acceptance retests.
Reservation is conservative across crashes and is never automatically refunded when side effects are uncertain.
An unchanged grant carries its cumulative consumption across specification revisions; approving another specification is not a budget reset.
Changing, renaming or renewing a grant requires a fresh actual owner-authority source, not reuse of an earlier permission.
The interface does not make network calls or turn budget data into a command.
The trusted calling path must enforce its approved operation, serialize operations and reconcile uncertain effects before another attempt.
No outreach, spend, destructive or security-sensitive test is authorized merely by inclusion in a product contract.

The evaluator computes three distinct outcomes.
Slice acceptance covers the assigned obligations and names remaining product requirements.
Product acceptance requires every accepted obligation and integrated journey at the same candidate with the approved oracle, evidence modes and environment, without findings or limitations.
Delivery additionally needs exact-head landing proof, passing delivery checks and an actual recorded authority event; product acceptance alone is never a release.
The existing merge scripts retain all their independent authority and green-check restrictions.
