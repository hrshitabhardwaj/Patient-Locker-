import phonenumbers

from app.core.config import get_settings


def normalize_phone(raw: str) -> str:
    """Return the number in E.164 form (+919876543210), which WhatsApp delivery requires."""
    try:
        number = phonenumbers.parse(raw, get_settings().default_phone_region)
    except phonenumbers.NumberParseException:
        number = None
    if number is None or not phonenumbers.is_valid_number(number):
        raise ValueError("Enter a valid phone number (add the country code if it is not local).")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
