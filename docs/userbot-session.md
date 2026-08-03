# Userbot session lifecycle

The production userbot never asks for a phone number, login code or 2FA password. Interactive Telegram authorization is restricted to the one-time provisioning command.

## Files and permissions

Compose mounts two separate writable directories into the userbot:

- `${ROMATIC_DATA_DIR}/runtime/userbot` at `/app/var` for health and schedule state;
- `${ROMATIC_DATA_DIR}/userbot-session` at `/run/romatic-userbot-session` for the Telethon SQLite session only.

Prepare them on the host before provisioning:

```bash
install -d -m 0700 -o 10001 -g 10001 \
  "$ROMATIC_DATA_DIR/runtime/userbot" \
  "$ROMATIC_DATA_DIR/userbot-session"
```

The production process rejects a session directory accessible by group/other users, a symlinked session path, a session file not owned by the container user, or a session file with permissions broader than `0600`.

## First authorization

Create `.env`, `.env.bot` and `.env.userbot` from their example files. Then run the provisioning command as the same Compose service and user that will run production:

```bash
docker compose --env-file .env run --rm --no-deps userbot \
  python -m userbot.provision authorize
```

The command may read the phone, Telegram code and 2FA password from the terminal. It does not initialize the database, register handlers or start watchdogs.

Verify the session before deployment:

```bash
docker compose --env-file .env run --rm --no-deps userbot \
  python -m userbot.provision check
```

## Rotation and revocation

Rotate the current Telegram authorization and create a replacement session:

```bash
docker compose --env-file .env run --rm --no-deps userbot \
  python -m userbot.provision rotate
```

Revoke the active Telegram authorization and remove local SQLite files:

```bash
docker compose --env-file .env run --rm --no-deps userbot \
  python -m userbot.provision revoke
```

Stop the production userbot before rotation or revocation. Never copy the live SQLite file while the process is running.

## Recovery

A Telethon session is a credential, not ordinary cache data. Do not commit it, put it in the shared bot runtime directory, attach it to tickets, or include it in application backups without encryption and access control.

If the session is missing, corrupt, unauthorized or exposed:

1. stop the userbot service;
2. revoke the affected Telegram session in Telegram settings or with the `revoke` command when possible;
3. remove the local session and SQLite sidecars;
4. run `authorize` to create a fresh session;
5. run `check`;
6. start the userbot and confirm its Docker health is `healthy`.

The userbot writes `/app/var/userbot-health.json`. Docker marks the container unhealthy when the Telegram client is disconnected, authorization is absent, a supervised worker is failed/stopped, or the report becomes stale.

## Environment isolation

The host `.env` is only for Compose interpolation and PostgreSQL container values. The bot receives `.env.bot`; the userbot receives `.env.userbot`. This prevents `BOT_TOKEN` and Supervisor credentials from reaching the userbot and prevents `USERBOT_API_HASH` from reaching the bot.
