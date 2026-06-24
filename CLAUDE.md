# Repo conventions

## Config-accessible features MUST appear in config.json — not just in code

When you add a feature that is meant to be configured via `config.json`
(VeloVox's is at `~/.config/velovox/config.json`, with `readAloud` and
`speakWrite` sections), wiring it into the app with an optional field + a
code-side default is **not enough**. The default only gets written to a *fresh*
config; an existing file silently falls back to the code default, so the user can
never see or discover the knob to edit it.

Whenever a new config-backed feature is added, do ALL of:

1. Add the field to the relevant `*Config` sub-struct (Codable) in
   `VeloVox/Config.swift`.
2. Add it to that struct's `fallback`/defaults block in code.
3. **Add it to the on-disk config** so the user can actually see and edit it —
   both the committed `config.example.json` AND the user's real
   `~/.config/velovox/config.json`.

If a knob exists in the app but not in the user's config file, it is a bug.
The config file is the contract; the code default is only the safety net.
