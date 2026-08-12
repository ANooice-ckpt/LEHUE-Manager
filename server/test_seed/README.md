# TEST synthetic baseline

This directory is the only Git-versioned runtime snapshot. It contains simulated
data for manual local/server testing and must never contain formal-study data.

On TEST startup, LEHUE copies this baseline to `data/test` only when both runtime
SQLite databases are absent or still contain no records. Later TEST writes stay in the ignored runtime tree;
`git pull` therefore cannot overwrite an active test database.

PROD uses only `data/prod` and never installs this baseline.

The current baseline contains no administrator, 15 synthetic S0 candidates,
one running synthetic participant and one device pack. Each fresh TEST instance
creates its own PI account; web sessions are always empty in the baseline.
