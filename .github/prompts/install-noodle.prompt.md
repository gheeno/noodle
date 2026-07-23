---
mode: agent
description: Install the Noodle test framework on this machine (macOS / Windows 11) — installation only
---

# Install Noodle on this machine

Follow **[docs/llm-install.md](../../docs/llm-install.md)** in this repo
phase by phase — it is the complete agent runbook: OS detection, a
read-only preflight ("what's already here?"), per-phase commands with
expected output, a failure table, and the report-back format.

The contract (same as the `install-noodle` skill for Claude Code /
Copilot CLI — keep these three files saying the same thing):

1. **Installation only** — don't run tests, start BusterBlock/any test
   app, or generate a test unless explicitly asked.
2. **Detect the OS yourself** (macOS or Windows 11) — don't ask.
3. **Preflight first, then only do what's missing** — `noodle --version`
   already printing a version means most of the work is done; never redo
   or reinstall something that already works.
4. **Verify each phase's checkpoint before the next**; on a mismatch, use
   the runbook's failure table (§6) instead of guessing.
5. **New terminal after any PATH-changing step** before concluding a
   command "isn't found".
6. **Never disable TLS verification** for a corporate-proxy cert error.
7. Finish with the runbook's §7 report: what was already present and
   skipped, each checkpoint's observed output, what you did NOT do.
