# Reproducible Noodle runner. Base image ships the browsers + system deps
# Playwright needs, so CI doesn't have to apt-get them.
# ponytail: bump this tag when you bump the playwright pin in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -e ".[all]" && playwright install chromium

ENV NOODLE_HEADLESS=true
ENTRYPOINT ["noodle"]
CMD ["run", "--headless"]   # no path -> tests_dir from noodle.yaml (sample_feature_tests/)
