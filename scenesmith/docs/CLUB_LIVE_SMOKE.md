# Club Live Smoke Expectation

Use `tools/debug_club_generation.py` for repeatable live-provider acceptance checks without launching the UI.

Example:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe tools\debug_club_generation.py --guest "path\to\Guest A.md" --guest "path\to\Guest B.md" --npc "Guest A" --json
```

Acceptance:

- Dashboard and at least one generated NPC panel should report `source=live_ai` or `source=cached_ai` in the `generation` summary.
- `source=deterministic_fallback` proves resilience and typed fallback metadata, not live AI quality.
- If relevant Club AI caches were intentionally cleared or bypassed for the smoke, require `source=live_ai` for both dashboard and panel.
- Provider request summaries must remain safe: JSON mode and disabled thinking may be reported as compact options, but raw prompts, provider payloads, credentials, and diagnostics must not be printed in normal output.
- DeepSeek JSON mode improves valid-JSON output, but schema validation and grounding checks remain authoritative.
