# Phase 3c Journeys — Privacy lock

- id: JRN-120
  steps: Run the bot (dry) without DATA_KEY.
  expected: Works exactly as before with plaintext state.json; BOT ALIVE and daily summary show "🔓 Data not encrypted — add DATA_KEY".

- id: JRN-121
  steps: Add DATA_KEY with an existing plaintext state.json (copy of the live shape) and run.
  expected: state.json.enc created (AES-256-GCM), plaintext state.json removed from the working tree, all trades/cooldowns/scoreboard preserved after reload; no warning shown.

- id: JRN-122
  steps: Run with a WRONG DATA_KEY against an existing state.json.enc.
  expected: state.json.enc untouched (same bytes); one "⚠️ Can't unlock my memory — check the DATA_KEY secret" message; nothing saved.

- id: JRN-123
  steps: Flip one byte in state.json.enc and in one recorder .enc file; decrypt.
  expected: Both rejected as tampered; bot treats it like REQ-305; decrypt helper exits non-zero with a clear message.

- id: JRN-124
  steps: Recorder dry run with DATA_KEY.
  expected: Hourly files end in .jsonl.gz.enc; status.json is plaintext health only; decrypt helper round-trips a file to the original JSON lines.

- id: JRN-125
  steps: Set BOT_SETTINGS='{"MIN_TP1_FEE_MULT": 5, "OB_PENALTY": 10, "FOO": 1, "TOP_N_COINS": "abc"}'.
  expected: Fee floor 5 and OB penalty 10 applied; FOO and TOP_N_COINS ignored with "⚠️ Some BOT_SETTINGS ignored: FOO, TOP_N_COINS"; defaults kept for the rest.

- id: JRN-126
  steps: Capture logs of a full dry run (bot + recorder) in Actions-style mode; then with LOG_DETAIL=1.
  expected: Default logs contain no coin symbols, prices, scores, trade details, wallet addresses, headlines, settings or secrets (grep); LOG_DETAIL=1 shows details.

- id: JRN-127
  steps: Watchdog with encrypted state (last cycle 3 h ago, then 30 min ago).
  expected: "BOT DOWN" sent for 3 h, nothing for 30 min — works with encryption on and off.

- id: JRN-128
  steps: Check git tree after build.
  expected: backtest/research outputs gitignored; salt file committed; README has the DATA_KEY kid-level steps and the "old public history stays in git" note.
