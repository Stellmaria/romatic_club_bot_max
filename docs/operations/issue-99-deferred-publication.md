# Issue #99: deferred auction publication recovery

## Deployment order

1. Deploy application code and migration `019_deferred_auction_publication.sql`.
2. Confirm the bot and userbot are healthy. The migration adds `NOT VALID`
   constraints, which protect new writes without rewriting damaged rows.
3. Verify Telegram metadata for all six target rows. Never infer a channel post
   ID from sequence, time proximity, or a failed publication log.
4. Create a reviewed JSON plan and run the repair command without `--apply`.
5. Compare every printed `before` and `after` snapshot.
6. Run the same command with `--apply`.
7. Run the post-condition queries below and confirm no duplicate channel posts.

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
python scripts/repair_auction_publications.py --plan /secure/issue-99.json
python scripts/repair_auction_publications.py --plan /secure/issue-99.json --apply
```

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
repair tool changes only publication lifecycle columns in `auctions`.
