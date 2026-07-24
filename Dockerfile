# Reproducible Noodle runner. Base image ships the browsers + system deps
# Playwright needs, so CI doesn't have to apt-get them.
# ponytail: bump this tag when you bump the playwright pin in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -e ".[all]" && playwright install chromium

# NOOD_0177 — drop root. The base image ships `pwuser` with the browser deps
# already usable by it; without this the Chromium process, the test run, and
# the internet-facing noodle-mcp server all ran as uid 0 against a writable
# mounted volume. Browsers are exactly the process you least want privileged.
RUN chown -R pwuser /app
USER pwuser

ENV NOODLE_HEADLESS=true
# NOOD_0173 — structured logs by default in a container: one JSON object per
# line to stderr (OTel field names), which the platform log store ingests
# directly (12-factor XI). Override NOODLE_LOG_FORMAT=text for a human console.
ENV NOODLE_LOG_FORMAT=json
ENTRYPOINT ["noodle"]
CMD ["run", "--headless"]   # no path -> tests_dir from noodle.yaml (sample_feature_tests/)
