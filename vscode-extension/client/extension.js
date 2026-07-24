const fs = require("fs");
const os = require("os");
const path = require("path");
const { workspace, window } = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

// The README installs noodle into the workspace .venv, not the system
// python3 — so a bare "python3" only works when VS Code happens to inherit
// an activated-venv PATH. Prefer an explicit setting, then the workspace
// .venv, then a platform default (Windows has no python3.exe).
function findPython() {
  const configured = workspace.getConfiguration("noodle").get("pythonPath", "");
  if (configured) return configured;
  const win = process.platform === "win32";
  for (const folder of workspace.workspaceFolders ?? []) {
    const venv = path.join(
      folder.uri.fsPath,
      ".venv",
      ...(win ? ["Scripts", "python.exe"] : ["bin", "python"])
    );
    if (fs.existsSync(venv)) return venv;
  }
  return win ? "python" : "python3";
}

// NOOD_0176 — resolve how to launch the language server. Prefer the `noodle-lsp`
// console script (pyproject [project.scripts]) as an ABSOLUTE path: its shebang
// points at the exact interpreter that installed noodle, with the [lsp] extra —
// so hover works with zero `noodle.pythonPath` config. We search for the actual
// file rather than trust a bare "noodle-lsp" on PATH, because VS Code launched
// from Finder/Dock often has a stripped PATH that omits the uv-tool bin dir even
// when a terminal resolves it fine (same reason the MCP config stores an
// absolute path). Falls back to `<python> -m noodle.lsp.server` (old behaviour)
// when the script isn't found — e.g. a bare install without the entry point.
function findServer() {
  const win = process.platform === "win32";
  const binName = win ? "noodle-lsp.exe" : "noodle-lsp";
  const configured = workspace.getConfiguration("noodle").get("pythonPath", "");

  const dirs = [];
  if (configured) dirs.push(path.dirname(configured));                  // sibling of the chosen python
  for (const f of workspace.workspaceFolders ?? [])                     // Option A: workspace .venv
    dirs.push(path.join(f.uri.fsPath, ".venv", win ? "Scripts" : "bin"));
  // Dev-clone install: `noodle install-extension` symlinks us into
  // <clone>/vscode-extension/client/, so the clone's own .venv — which may be
  // on nobody's GUI PATH — sits two levels up from this file. realpathSync
  // resolves the symlink to the clone; a VSIX install just finds nothing here.
  try {
    const clone = path.resolve(fs.realpathSync(__dirname), "..", "..");
    dirs.push(path.join(clone, ".venv", win ? "Scripts" : "bin"));
  } catch (_e) { /* not resolvable — skip */ }
  dirs.push(path.join(os.homedir(), ".local", "bin"));                  // Option B: uv-tool default shim dir
  for (const d of (process.env.PATH || "").split(path.delimiter)) if (d) dirs.push(d);

  for (const d of dirs) {
    const p = path.join(d, binName);
    if (fs.existsSync(p)) return { command: p, args: [] };             // console script — no python guess
  }
  return { command: findPython(), args: ["-m", "noodle.lsp.server"] }; // fallback
}

function activate(context) {
  const config = workspace.getConfiguration("noodle");
  const { command, args } = findServer();
  const severity = config.get("unknownStepSeverity", "warning");

  const serverOptions = {
    command,
    args,
    transport: TransportKind.stdio,
    options: {
      env: { ...process.env, NOODLE_UNKNOWN_STEP_SEVERITY: severity },
    },
  };

  const clientOptions = {
    // Files mapped to the "noodle" language — via the workspace
    // files.associations glob that `noodle init` scaffolds (NOOD_0174), not a
    // global .feature claim, so we don't collide with Cucumber extensions.
    documentSelector: [{ scheme: "file", language: "noodle" }],
    synchronize: {
      // re-validate when .env changes (variable completions update)
      fileEvents: workspace.createFileSystemWatcher("**/.env"),
    },
  };

  client = new LanguageClient(
    "noodle-lsp",
    "Noodle Language Server",
    serverOptions,
    clientOptions
  );

  client.start().catch((err) => {
    window.showErrorMessage(
      `Noodle LSP failed to start (tried "${command}"): ${err.message}\n` +
      `Install noodle with LSP extras (uv pip install -e ".[lsp]") and, if it ` +
      `lives outside this workspace's .venv, point "noodle.pythonPath" at that interpreter.`
    );
  });
}

function deactivate() {
  if (client) return client.stop();
}

module.exports = { activate, deactivate };
