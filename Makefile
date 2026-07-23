.PHONY: test test-wok-web test-wok-mobile test-wok-desktop test-wok-performance lint vsix install-ext clean

# Run all tests (no browser required)
test:
	python -m pytest unit_tests/ -v

# Per-wok test isolation (NOOD_0155) — regression-check one capability
# work area without running the others. `test-wok-web` adds the pre-wok
# broad web suite, which lives flat in unit_tests/.
test-wok-web:
	python -m pytest unit_tests/ --ignore=unit_tests/woks -v unit_tests/woks/web
test-wok-mobile:
	python -m pytest unit_tests/woks/mobile unit_tests/woks/test_wok_registry.py -v
test-wok-desktop:
	python -m pytest unit_tests/woks/desktop unit_tests/woks/test_wok_registry.py -v
test-wok-performance:
	python -m pytest unit_tests/woks/performance unit_tests/woks/test_wok_registry.py -v

# Lint (uv pip install -e ".[dev]" first)
lint:
	ruff check .

# Build the VS Code extension .vsix package
# Requires: cd vscode-extension && npm install  (already done)
# Requires: npm install -g @vscode/vsce
vsix:
	cd vscode-extension && npx @vscode/vsce package --allow-missing-repository --skip-license --out ../noodle-$(shell python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['version'])").vsix

# Install the extension directly into VS Code (skips marketplace).
# --force: the extension manifest's own version (vscode-extension/package.json)
# doesn't track pyproject.toml's, so VS Code can see the "same version already
# installed" and silently skip the reinstall without it — the classic "it
# says installed but .feature files are still uncoloured" report.
install-ext: vsix
	code --install-extension noodle-$(shell python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['version'])").vsix --force

# Remove build artefacts
clean:
	rm -f noodle-*.vsix
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
