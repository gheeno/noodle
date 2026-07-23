# Cross-wok composition (NOOD_0155) — desktop wok + web wok in ONE scenario:
# read a value out of an Excel workbook (resources/inventory.xlsx), then use
# it to drive and assert a web page. Spreadsheet steps are browserless and
# stdlib-only, so they compose into any scenario on any OS with no extras —
# this is the "get a value from Excel into a web test" flow from docs/woks.md.
#
# The web side targets the bundled BusterBlock app; start it first
# (docs/manual.md § BusterBlock) and set APP in environments.yaml/.env.
#
# Run:  noodle run sample_feature_tests/desktop/ --tag excel
@web @excel @capability
Feature: Desktop + web woks — an Excel value drives a web test

  Scenario: Search the catalog for the movie named in the spreadsheet
    Given User reads cell "B2" from sheet "Catalog" of spreadsheet "inventory.xlsx" into "TITLE"
    And User is on "{env:APP}"
    When User searches for "{var:TITLE}"
    Then User should see "{var:TITLE}"

  Scenario: The workbook itself can be asserted directly
    Then User expects cell "A1" of spreadsheet "inventory.xlsx" to equal "Movie"
    And User expects cell "B2" of sheet "Catalog" of spreadsheet "inventory.xlsx" to equal "Blade Runner"
