import re

_VALUE_PATTERNS = [
    (re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'), "EMAIL"),
    (re.compile(r'^4[0-9]{12}(?:[0-9]{3})?$'), "CARD_NUMBER"),       # Visa
    (re.compile(r'^5[1-5][0-9]{14}$'), "CARD_NUMBER"),                # Mastercard
    (re.compile(r'^3[47][0-9]{13}$'), "CARD_NUMBER"),                  # Amex
    (re.compile(r'^[0-9]{3,4}$'), "CARD_CVV"),                        # CVV
    (re.compile(r'^[0-9]{4,5}$'), "POSTAL_CODE"),
]

_FIELD_HINTS = {
    "password": "PASSWORD",
    "passwd": "PASSWORD",
    "secret": "SECRET",
    "token": "TOKEN",
    "card": "CARD_NUMBER",
    "cvv": "CARD_CVV",
    "cvc": "CARD_CVV",
    "zip": "POSTAL_CODE",
    "postal": "POSTAL_CODE",
    "email": "EMAIL",
    "phone": "PHONE",
    "ssn": "SSN",
}


def suggest_var_name(field_name: str) -> str:
    """Turn 'email field' → 'EMAIL', 'card number' → 'CARD_NUMBER'."""
    normalised = field_name.lower().strip()
    for hint, var in _FIELD_HINTS.items():
        if hint in normalised:
            return var
    # Generic: uppercase and underscore-join the field name words
    return re.sub(r'\W+', '_', normalised).upper().strip('_') or "VALUE"


def redact(value: str, field_name: str = "") -> tuple[str, str | None]:
    """
    Return (placeholder_or_value, env_var_name_or_None).

    If the value looks sensitive, returns ("[VAR_NAME]", "VAR_NAME").
    Otherwise returns (value, None).
    """
    # Check field name hints first (stronger signal than value patterns)
    field_var = None
    normalised_field = field_name.lower()
    for hint, var in _FIELD_HINTS.items():
        if hint in normalised_field:
            field_var = var
            break

    # Check value patterns
    value_var = None
    for pattern, var in _VALUE_PATTERNS:
        if pattern.match(value.strip()):
            value_var = var
            break

    var_name = field_var or value_var
    if var_name:
        return f"[{var_name}]", var_name

    return value, None
