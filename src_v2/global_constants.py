"""Store constants that apply to every invoice-pipeline run."""

RUN_YEAR = 2026
LOCATION_CODE = "NLF"
LOCATION_NAME = "Nine Lives Foundation"
MANIFEST_FILENAME = "manifest.json"
SERVICE_CATALOG_FILENAME = "service_catalog.json"
OUTPUT_DIRECTORY_NAME = "bac-outputs"
LOG_DIRECTORY_NAME = "logs"

AIRTABLE_ENV_VARS = {
    "token": "AIRTABLE_TOKEN",
    "base_id": "AIRTABLE_BASE_ID",
    "appointments_table_id": "AIRTABLE_APPOINTMENTS_TABLE_ID",
    "cats_table_id": "AIRTABLE_CATS_TABLE_ID",
}
