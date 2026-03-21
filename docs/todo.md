# TODO

- Add duplicate-detection logic so papers already captured in prior daily or weekly notes are skipped or merged cleanly.
- Revisit the external LLM CLI integration once the exact non-interactive invocation is confirmed for the target environment.
- Surface Claude slash-skill permission failures explicitly and decide how the app should react when the note-generation command returns without creating a file.
- Cache arXiv results or add a dedicated simulation/backfill path so multi-day replays do not refetch the full feed for each day.
