@web
Feature: File Download
  Covers: clicking a link that downloads a file.

  # The /download page lists whatever's been uploaded to this shared public
  # demo site by anyone, anywhere — it isn't a fixed set of files, so a
  # hardcoded filename (e.g. "vendor.txt") can vanish at any time. Upload our
  # own fixture first so the scenario controls the filename it depends on.
  Background:
    Given User is on "{env:THE_INTERNET}/upload"
    When User uploads "sample_feature_tests/web/the_internet/resources/data/sample.txt" to the "file upload input"
    And User clicks the "Upload" button
    And User is on "{env:THE_INTERNET}/download"

  @smoke
  Scenario: Downloading a text file
    When User clicks "sample.txt"
    Then a file "sample.txt" should be downloaded
