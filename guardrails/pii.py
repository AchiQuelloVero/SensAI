import re

PII_TYPES = ("EMAIL", "IBAN", "CARD", "PHONE")


def luhn_valid(number):
    digits = [int(d) for d in number if d.isdigit()]
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def iban_valid(iban):
    iban = iban.replace(" ", "")
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


DETECTORS = {
    "EMAIL": (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), None),
    "IBAN": (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"), iban_valid),
    "CARD": (re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)"), luhn_valid),
    "PHONE": (re.compile(r"(?<![\w+])(?<!\d[ .-])(?:\+|\()?\d(?:[ .()-]*\d){8,14}(?![ .-]?\d)(?!\w)"), None),
}


def mask_pii(text, types=PII_TYPES):
    counts = {}
    for name in PII_TYPES:
        if name not in types:
            continue
        pattern, is_valid = DETECTORS[name]

        def replace(match):
            if is_valid is not None and not is_valid(match.group()):
                return match.group()
            counts[name] = counts.get(name, 0) + 1
            return f"[{name}]"

        text = pattern.sub(replace, text)
    return text, counts
