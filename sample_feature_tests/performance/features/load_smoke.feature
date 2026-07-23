# Performance wok smoke (NOOD_0155) — the built-in load generator: plain
# Gherkin load tests with latency/error/throughput gates. No browser, no
# extra dependencies (stdlib threads + urllib). The chart step renders a
# latency-over-time PNG through the screenshot pipeline into Allure + RCA.
#
# Point APP at any HTTP endpoint you are allowed to load-test — e.g. the
# bundled BusterBlock app started locally (docs/manual.md § BusterBlock).
# Keep user counts polite: this is a CI performance *gate*, not a stress
# farm — graduate to Locust for heavy or distributed load (docs/woks.md).
#
# Run:  noodle run sample_feature_tests/performance/ --tag perf
@perf @capability
Feature: Performance wok — load-test gates in plain Gherkin

  Scenario: The home page holds its latency budget under light load
    When User runs a load test on "{env:APP}" with 5 users for 10 seconds
    Then the p95 response time should be under 800 ms
    And the average response time should be under 400 ms
    And the error rate should be under 1 %
    And User saves the load test report as "home page baseline"

  Scenario: A fixed request budget with a throughput floor
    When User runs a load test on "{env:APP}" with 50 requests using 5 users
    Then the throughput should exceed 5 requests per second
    And User stores the p95 response time into "HOME_P95"
