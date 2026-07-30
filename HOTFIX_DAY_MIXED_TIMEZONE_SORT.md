# Hotfix: /day mixed timezone sorting

- Normalize every `start_time` with `ensure_utc()` before sorting.
- Supports legacy naive Moscow timestamps and newer timezone-aware values in the same result set.
- Historical `/day` output now includes rows with status `finished`.
- Added a regression test where naive and aware timestamps coexist in both completed and active groups.
