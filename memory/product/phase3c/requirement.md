# Phase 3c — Privacy lock on free GitHub ("Plan B") (phase-3c)

## Goal
The repo is public and the Oracle signup failed (decide later). Stay on free GitHub Actions but make everything valuable unreadable to outsiders: encrypt the bot's state and all recorded data, move tunable/"secret-sauce" settings into a GitHub secret, and make public Actions logs say almost nothing. Code stays visible; data, results and settings do not. Must roll out safely: until the user adds the key, everything keeps working unencrypted with a visible warning.

## Functional requirements

### Key
- REQ-300 [always] The system shall derive a 256-bit key from the GitHub secret DATA_KEY (a passphrase the user makes up, ≥ 6 random words) using scrypt with a fixed, committed salt file.
- REQ-301 [unwanted] If DATA_KEY is missing, then the system shall keep working unencrypted and show "🔓 Data not encrypted — add DATA_KEY" in BOT ALIVE and the daily summary.
- REQ-302 [always] The system shall never print DATA_KEY or the derived key, and the workflows shall ::add-mask:: it.

### Encrypted state
- REQ-303 [event] When DATA_KEY is present, the live bot shall store its state as state.json.enc (AES-256-GCM, authenticated) instead of state.json.
- REQ-304 [event] When DATA_KEY is first present and a plaintext state.json exists, the system shall migrate it into state.json.enc and delete state.json from the working tree in the same commit (git history keeps old public versions — stated in README).
- REQ-305 [unwanted] If state.json.enc cannot be decrypted (wrong key), then the system shall NOT overwrite it, shall send "⚠️ Can't unlock my memory — check the DATA_KEY secret" once per run, and shall run with an in-memory empty state that is not saved (interpreted).
- REQ-306 [always] The watchdog shall read last-cycle time from the encrypted state using DATA_KEY (or from a minimal plaintext heartbeat file containing only a timestamp — builder's choice, documented).
- REQ-307 [always] The commit/push logic (fetch/reset/restore/push) shall treat state.json.enc exactly like state.json today.

### Encrypted recorded data
- REQ-308 [event] When DATA_KEY is present, the recorder shall write each hourly file as <name>.jsonl.gz.enc (AES-256-GCM).
- REQ-309 [always] The recorder's status.json shall contain only health info (per-source last time + counts), no market data, so the live bot can read it without the key.
- REQ-310 [always] A small decrypt helper (CLI) shall let research code read encrypted state and data files with DATA_KEY.

### Hidden settings
- REQ-311 [always] The live bot shall read optional JSON overrides from the secret BOT_SETTINGS for all tunable strategy settings (grade thresholds, ATR multiples, fee floor, OB penalty, hold times, exclusions, coin count, derivatives switches, and future strategy parameters), falling back to code defaults.
- REQ-312 [unwanted] If BOT_SETTINGS is not valid JSON or contains unknown keys or invalid values, then the system shall ignore only the bad parts, keep defaults, and show "⚠️ Some BOT_SETTINGS ignored: <keys>" in BOT ALIVE.

### Quiet public logs
- REQ-313 [always] In GitHub Actions the bot and recorder shall log only health lines (run start/end, cycle number, cycle seconds, counts, error class names) — no coins, scores, prices, trades, wallets, headlines or settings.
- REQ-314 [optional] Where LOG_DETAIL=1 is set, the system shall log full details (for local debugging only).
- REQ-315 [always] Exception messages written to logs shall be reduced to the exception class and a short sanitized reason with URLs, tokens and numbers removed (interpreted).

### Public repo hygiene
- REQ-316 [always] Research and backtest outputs (backtest_report.md, backtest_results.json, research_report.md, CSVs) shall be gitignored from now on; results are shared with the user in chat/Telegram instead.
- REQ-317 [always] README shall explain in kid-level steps how to create DATA_KEY (make up 6+ random words, store it safely offline, add as GitHub secret) and warn that losing it means the bot starts with a fresh memory.

## Quality requirements
```
Q-30 Tag: Privacy.PublicLeak
     Scale: coins, prices, trades, scores, wallets, headlines or settings visible in public Actions logs or new public files after rollout
     Meter: grep a full run's logs + git tree of main and data branches
     Must: 0   Plan: 0
Q-31 Tag: Safety.NoLockout
     Scale: runs where a missing/wrong key destroys or overwrites existing encrypted state
     Meter: tests with missing key, wrong key, rotated key
     Must: 0   Plan: 0
Q-32 Tag: Crypto.Correctness
     Scale: tampered ciphertexts accepted
     Meter: flip bytes in state.json.enc and a data file
     Must: 0   Plan: 0
```
