# Runtime dependency inventory

Issue #46 is handled as an inventory-first migration. This contract does not remove
packages and does not claim that a static non-observation proves a dependency unused.

## Sources of truth

- `requirements/bot.in` and `requirements/userbot.in` define direct runtime
  dependencies.
- Their hashed lock files define the complete installed graphs.
- `quality/runtime-dependency-policy.json` records service boundaries, forbidden
  cross-service packages, source roots and Docker/Compose build markers.
- `scripts/runtime_dependency_inventory.py` validates the policy and produces a
  deterministic JSON report with lock hashes, package lists and static import evidence.

## Commands

```bash
python -m scripts.runtime_dependency_inventory validate
python -m scripts.runtime_dependency_inventory report \
  --output var/quality/runtime-dependency-inventory.json
```

The `direct_distributions_not_observed_in_static_imports` field is a review queue,
not an automatic removal list. A package may be loaded dynamically, used by an
entrypoint, required for native acceleration, or imported through a compatibility
boundary. Removal requires a separate PR with static evidence, runtime image smoke,
and rollback conditions.
