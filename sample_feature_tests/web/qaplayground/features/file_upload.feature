@web
Feature: QA Playground — File Upload and Download
  Covers: file upload input, download buttons.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/file-upload"

  @smoke
  Scenario: Upload page renders
    Then User should see "Select file to upload"

  Scenario: Upload a file
    When User uploads "sample_feature_tests/web/qaplayground/resources/data/sample.txt" to the file upload input
    Then User should see "File uploaded successfully!"

  Scenario: Download a PDF
    When User clicks "Download PDF"
    Then a file should be downloaded
