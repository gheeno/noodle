@web
Feature: File Upload
  Covers: uploading a local file through a native <input type=file>.

  Background:
    Given User is on "{env:THE_INTERNET}/upload"

  @smoke
  Scenario: Uploading a file shows its name on the result page
    When User uploads "sample_feature_tests/web/the_internet/resources/data/sample.txt" to the "file upload input"
    And User clicks the "Upload" button
    Then User should see "File Uploaded!"
    And User should see "sample.txt"
