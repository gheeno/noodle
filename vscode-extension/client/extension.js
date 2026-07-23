const fs = require("fs");
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

function activate(context) {
  const config = workspace.getConfiguration("noodle");
  const python = findPython();
  const severity = config.get("unknownStepSeverity", "warning");

  const serverOptions = {
    command: python,
    args: ["-m", "noodle.lsp.server"],
    transport: TransportKind.stdio,
    options: {
      env: { ...process.env, NOODLE_UNKNOWN_STEP_SEVERITY: severity },
    },
  };

  const clientOptions = {
    // activate for .feature files registered as noodle language
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
      `Noodle LSP failed to start (tried "${python}"): ${err.message}\n` +
      `Install noodle with LSP extras (uv pip install -e ".[lsp]") and, if it ` +
      `lives outside this workspace's .venv, point "noodle.pythonPath" at that interpreter.`
    );
  });
}

function deactivate() {
  if (client) return client.stop();
}

module.exports = { activate, deactivate };
