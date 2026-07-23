from pathlib import Path

from noodle.recorder import sensitives

# Injected into every page to capture click and fill events without CSS selectors.
_EVENT_SCRIPT = """
(function() {
  if (window.__noodle_attached) return;
  window.__noodle_attached = true;

  function getLabel(el) {
    return el.getAttribute('aria-label')
      || el.getAttribute('placeholder')
      || el.getAttribute('name')
      || (el.innerText || '').trim().slice(0, 60)
      || el.getAttribute('id')
      || el.tagName.toLowerCase();
  }

  document.addEventListener('click', function(e) {
    var label = getLabel(e.target);
    if (label) window.__noodle_event('click', { label: label });
  }, true);

  document.addEventListener('change', function(e) {
    var el = e.target;
    var tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      window.__noodle_event('fill', { field: getLabel(el), value: el.value });
    }
  }, true);
})();
"""


class Recorder:
    def __init__(self, output_path: str, feature_name: str = "Recorded Feature"):
        self.output_path = output_path
        self.feature_name = feature_name
        self.steps: list[str] = []
        self._last_url: str = ""
        # Track fills to deduplicate: same field+value fired by both change + click on submit
        self._last_fill: tuple[str, str] = ("", "")

    def record(self):
        """Open a visible browser, record until the user closes it, write .feature file."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            self._attach_listeners(page)
            print(
                "\n  [Noodle Recorder] Browser open — perform your test flow, "
                "then close the browser window to save.\n"
            )
            try:
                # Wait indefinitely until the page (or browser) closes
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        self._write_feature()

    # ------------------------------------------------------------------

    def _attach_listeners(self, page):
        page.expose_function("__noodle_event", self._on_event)
        page.add_init_script(_EVENT_SCRIPT)
        page.on("framenavigated", lambda frame: self._on_navigate(frame.url) if frame == page.main_frame else None)

    def _on_event(self, event_type: str, data: dict):
        if event_type == "click":
            self._on_click(data.get("label", "element"))
        elif event_type == "fill":
            self._on_fill(data.get("field", "field"), data.get("value", ""))

    def _on_navigate(self, url: str):
        # Skip non-http URLs (about:blank, chrome-extension:// etc.)
        if not url.startswith("http"):
            return
        if url == self._last_url:
            return
        self._last_url = url
        if not self.steps:
            self.steps.append(f'Given User is on "{url}"')
        else:
            self.steps.append(f'When User navigates to "{url}"')

    def _on_click(self, label: str):
        label = label.strip()
        if not label or label.lower() in ("", "body", "html", "div", "span"):
            return
        self.steps.append(f'When User clicks "{label}"')

    def _on_fill(self, field: str, value: str):
        field = field.strip()
        value = value.strip()
        if not value:
            return
        # Deduplicate: skip if identical to last fill
        if (field, value) == self._last_fill:
            return
        self._last_fill = (field, value)
        placeholder, var_name = sensitives.redact(value, field)
        if var_name:
            self.steps.append(f'When User enters {placeholder} in the {field} field')
        else:
            self.steps.append(f'When User enters "{value}" in the {field} field')

    def _write_feature(self):
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"Feature: {self.feature_name}",
            "",
            "  @web",
            f"  Scenario: {self.feature_name}",
            "",
        ]
        for i, step in enumerate(self.steps):
            if i == 0:
                lines.append(f"    {step}")
            else:
                for kw in ("Given ", "When ", "Then "):
                    if step.startswith(kw):
                        lines.append(f"    And {step[len(kw):]}")
                        break
                else:
                    lines.append(f"    And {step}")
        lines.append("")
        Path(self.output_path).write_text("\n".join(lines))
        print(f"\n  [Noodle Recorder] Feature written to: {self.output_path}\n")
