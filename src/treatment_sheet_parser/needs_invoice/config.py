"""Define the Airtable schema and supported values for Needs Invoice queries."""

from datetime import date

# Airtable became authoritative after this date; the cutoff itself is excluded.
CUTOFF_DATE = date(2025, 4, 28)

# These clauses reproduce the Version 1 Needs_Invoice view criteria.
STATUS_FILTER = "OR({Status}='Completed',{Status}='Scheduled',{Status}='Needs Scheduling')"
INCOMPLETE_FILTER = "OR({Status}!='Completed',{Cost}=BLANK(),{Filled}=BLANK(),{Invoice}=BLANK())"

# Canonical output concepts mapped to exact Cat-table field names.
CAT_FIELD_MAP = {
    "cat_name": "Cat Name",
    "gender": "Gender",
    "microchip_number": "Microchip",
    "color": "Color",
    "owner_or_trapper": "Trapper or Owner",
    "address": "Address",
    "age": "Age",
    "ear_tip": "Ear Tip",
    "vouchers": "Voucher",
}

# Canonical output concepts mapped to exact Appointments-table field names.
APPOINTMENT_FIELD_MAP = {
    "type": "Type",
    "owner_or_trapper": "Owner or Trapper",
    "cat_address": "Cat Address",
    "cat_city": "Cat City",
    "services": ("Services", "Additional Services"),
    "total_cost": "Cost",
}

# Typed fields contain values and linked-record IDs used for validation and output.
APPOINTMENT_FIELDS = (
    "Date",
    "Location",
    "Cats",
    APPOINTMENT_FIELD_MAP["type"],
    APPOINTMENT_FIELD_MAP["owner_or_trapper"],
    APPOINTMENT_FIELD_MAP["cat_address"],
    APPOINTMENT_FIELD_MAP["cat_city"],
    *APPOINTMENT_FIELD_MAP["services"],
    APPOINTMENT_FIELD_MAP["total_cost"],
)
CAT_FIELDS = tuple(dict.fromkeys(CAT_FIELD_MAP.values()))

# String-format reads translate selected linked-record IDs into human-readable labels.
APPOINTMENT_DISPLAY_FIELDS = (APPOINTMENT_FIELD_MAP["owner_or_trapper"],)
CAT_DISPLAY_FIELDS = (CAT_FIELD_MAP["owner_or_trapper"], CAT_FIELD_MAP["vouchers"])

# Accepted single-select values mirror the current production Airtable schema.
APPOINTMENT_TYPES = {"Pet", "TNR", "BAC Foster", "Intake"}
CAT_AGES = {"Adult", "Neonate (0-4 Weeks)", "Weaned (4-8 Weeks)", "Juvenile", "Unknown"}
EAR_TIPS = {"None", "Yes - Left", "Yes - Right"}
