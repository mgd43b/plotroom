# Clerk triggers

## T1: #41 merges (tell a waiting lane)
- Message `relay:fixture-worker`: "Fixture #[N] at [sha8]. Literal $(fixture-only)."
- Tell the coordinator.

## T2: branch `fixture/completed*` opens a PR that merges
- Tell the coordinator: "Historical [sha8]."
- `FIRED 2026-11-23 12:00Z abcdef1`

## T3: the PR that repairs the fixture merges
- Tell the coordinator: "Investigate."
