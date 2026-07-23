---
name: "install-noodle"
description: "Install or set up the Noodle test framework on this machine, macOS or Windows 11, fresh or partially installed. Use whenever asked to 'install noodle', 'set up noodle', get a new machine/laptop ready for Noodle, or fix a broken Noodle install — installation only, distinct from writing or running tests (that's the noodle skill)."
domain: "testing"
confidence: "high"
source: "team-decision"
---

# Install Noodle — agent skill

The complete runbook is **`docs/llm-install.md`** in this repo — read it
now and follow it phase by phase. It is written for you: OS detection,
a read-only preflight ("what's already here?"), per-phase commands with
expected output, a failure table, and the report-back format. This skill
only pins the contract; the runbook is the procedure.

The contract, in brief:

1. **Installation only.** Don't run tests, start BusterBlock/any test app,
   or generate a test unless the user explicitly asked for that too.
2. **Detect the OS yourself** (macOS or Windows 11) — don't ask.
3. **Preflight first, then only do what's missing.** `noodle --version`
   already printing a version means most of the work is done — never redo
   or reinstall something that already works. One exception (NOOD_0133):
   the noodle package install itself always starts clean-slate — uninstall
   from BOTH `pip` and `uv tool` (best-effort) before the editable
   install, and finish by verifying the shim resolves into the clone
   (`which -a noodle` / `Get-Command noodle -All`), per the runbook's §3.
   A stale non-editable copy on PATH silently shadows every fix.
4. **Verify each phase against its checkpoint before the next**; fix
   mismatches via the runbook's failure table (§6) instead of guessing.
5. **New terminal after any PATH-changing step** (`uv tool update-shell`,
   winget installs) before concluding a command "isn't found".
6. **Never disable TLS verification** to get past a corporate-proxy
   certificate error.
7. Finish with the runbook's §7 report: what was skipped as already
   present, each checkpoint's observed output, what you did NOT do.

If `docs/llm-install.md` isn't on disk (you're outside the noodle repo),
clone the repo first — `git clone https://github.com/gheeno/noodle.git` —
since the install needs the clone anyway.
