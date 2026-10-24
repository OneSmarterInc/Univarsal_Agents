# Keys (for admins)

One key = **one project + one agent**. A project gets a separate key for each agent it uses.

```
agents create-key --project kormic --agent cv --label "Kormic production"
agents create-key --project kormic --agent linkedin --label "Kormic production"
```

The key is printed **once**. Only a hash is stored, so a lost key cannot be shown
again: make a new one and revoke the old one.

| Task | Command |
|---|---|
| List keys | `agents list-keys` or `agents list-keys --project kormic` |
| Block a key | `agents revoke-key key_3f9c0a1b2c3d4e5f` |
| See usage | `agents stats --days 30` |

## Rules

- **Project names:** lower-case letters, digits, `-` and `_` (for example `kormic`, `hr-portal`).
- **Hand keys over privately** (a password manager or the project's secret store), never in chat or email.
- **Keys live in the project's backend settings only**, never in a website or mobile app.
- **Rotating a key:** create the new key, update the project, then revoke the old key.
  Both keys work during the switch.
- **A leaked key** affects only that project and that agent: revoke it and make a new one.
- Projects see only their own jobs. Another project's job id answers 404.

## What is recorded

Key creation, revocation, job creation and cancellation are written to the
`audit_events` table with time, project, agent, key id and job id (never file content).
