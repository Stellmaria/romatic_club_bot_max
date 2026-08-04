# Issue #99: deferred auction publication recovery

## Deployment order

1. Deploy application code and migration `019_deferred_auction_publication.sql`.
2. Confirm the bot and userbot are healthy. The migration adds `NOT VALID`
   constraints, which protect new writes without rewriting damaged rows.
3. At userbot startup, the bounded issue #99 recovery reads the three known
   discussion roots, verifies their forwarded source channel, verifies the two
   known channel posts, and searches the `9243` time window for exactly one post
   whose text contains the exact lot number.
4. The runtime recovery first executes the transaction as a dry run. It applies
   only proved mappings, preserves bids/owners/audit/outbox history, validates
   both constraints, and never republishes a lot. Ambiguous or missing Telegram
   evidence is logged and left for manual review.
5. The bot finalizer processes restored expired rows through the normal winner
   workflow. A restored expired row is not considered complete while it remains
   merely `active`.
6. Use the reviewed JSON command below only when runtime discovery reports an
   unresolved mapping or when an operator needs an auditable manual replay.

## Required verification

- `9210`: obtain the positive channel post ID from discussion root `1148772`.
- `9217`: obtain it from discussion root `1149339`.
- `9221`: obtain it from discussion root `1149326`.
- `9243`: inspect the auction channel around `2026-08-03 16:30 UTC`. Use
  `confirm` when the post exists. Use `requeue` only after an operator records
  `post_verified_absent=true`.
- `3797`: verify that channel post `5927` is the auction post, then use
  `normalize_published`.
- `7523`: verify that channel post `10139` is the auction post, then use
  `normalize_published`.

## Plan example

```json
{
  "repairs": [
    {
      "auction_id": 9210,
      "action": "confirm",
      "channel_message_id": 12345,
      "discussion_message_id": 1148772
    },
    {
      "auction_id": 9217,
      "action": "confirm",
      "channel_message_id": 12346,
      "discussion_message_id": 1149339
    },
    {
      "auction_id": 9221,
      "action": "confirm",
      "channel_message_id": 12347,
      "discussion_message_id": 1149326
    },
    {
      "auction_id": 9243,
      "action": "requeue",
      "post_verified_absent": true
    },
    {
      "auction_id": 3797,
      "action": "normalize_published",
      "channel_message_id": 5927
    },
    {
      "auction_id": 7523,
      "action": "normalize_published",
      "channel_message_id": 10139
    }
  ]
}
```

```bash
python scripts/repair_auction_publications.py \
  --plan /secure/issue-99.json \
  --dry-run
python scripts/repair_auction_publications.py \
  --plan /secure/issue-99.json \
  --apply
python scripts/repair_auction_publications.py --validate-constraints
```

Dry-run is also the default when neither `--apply` nor
`--validate-constraints` is specified.

## Post-conditions

```sql
SELECT auction_id, status, message_id, discussion_message_id
FROM public.auctions
WHERE message_id <= 0
   OR (
       status IN ('scheduled', 'publishing', 'publication_deferred')
       AND message_id IS NOT NULL
   );

SELECT conname, convalidated
FROM pg_constraint
WHERE conrelid = 'public.auctions'::regclass
  AND conname IN (
      'chk_auctions_message_id_positive',
      'chk_auctions_unpublished_state_has_no_message'
  );
```

The first query must return no rows and both constraints must be validated.
Check bids, winners, audit history, and outbox history for all six auctions. The
repair layer hashes those protected records before and after every transaction
and aborts if they change. It changes only publication lifecycle columns in
`auctions`; final winner processing remains the normal bot finalizer's job.
