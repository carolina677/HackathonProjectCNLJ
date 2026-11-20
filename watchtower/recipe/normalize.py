"""Turn portal strings into contract values. Every function returns None rather than guess."""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import dateparser

from ..models import ContractNature, ProcedureType, TenderStatus

COUNTRY_TZ = {
    "DE": "Europe/Berlin", "AT": "Europe/Vienna", "CH": "Europe/Zurich", "FR": "Europe/Paris",
    "BE": "Europe/Brussels", "NL": "Europe/Amsterdam", "LU": "Europe/Luxembourg", "ES": "Europe/Madrid",
    "PT": "Europe/Lisbon", "IT": "Europe/Rome", "PL": "Europe/Warsaw", "CZ": "Europe/Prague",
    "SK": "Europe/Bratislava", "HU": "Europe/Budapest", "SI": "Europe/Ljubljana", "HR": "Europe/Zagreb",
    "RO": "Europe/Bucharest", "BG": "Europe/Sofia", "GR": "Europe/Athens", "CY": "Asia/Nicosia",
    "MT": "Europe/Malta", "IE": "Europe/Dublin", "DK": "Europe/Copenhagen", "SE": "Europe/Stockholm",
    "FI": "Europe/Helsinki", "EE": "Europe/Tallinn", "LV": "Europe/Riga", "LT": "Europe/Vilnius",
    "NO": "Europe/Oslo", "IS": "Atlantic/Reykjavik", "GB": "Europe/London",
}
COUNTRY_LANG = {"DE": "de", "AT": "de", "CH": "de", "FR": "fr", "BE": "fr", "LU": "fr", "NL": "nl", "ES": "es",
                "PT": "pt", "IT": "it", "PL": "pl", "CZ": "cs", "SK": "sk", "HU": "hu", "SI": "sl", "HR": "hr",
                "RO": "ro", "BG": "bg", "GR": "el", "DK": "da", "SE": "sv", "FI": "fi", "EE": "et", "LV": "lv",
                "LT": "lt", "IE": "en", "MT": "en", "GB": "en", "NO": "nb"}
COUNTRY_NAMES = {
    "DE": ["deutschland", "germany", "allemagne", "duitsland", "alemania", "germania", "niemcy"],
    "AT": ["österreich", "austria", "autriche", "oostenrijk"],
    "FR": ["france", "frankreich", "frankrijk", "francia", "francja"],
    "NL": ["nederland", "netherlands", "niederlande", "pays-bas", "países bajos", "paesi bassi", "holandia"],
    "BE": ["belgique", "belgië", "belgium", "belgien", "bélgica", "belgio", "belgia"],
    "ES": ["españa", "spain", "spanien", "espagne", "spanje", "spagna", "hiszpania"], "PT": ["portugal"],
    "IT": ["italia", "italy", "italien", "italie", "italië", "włochy"],
    "PL": ["polska", "poland", "polen", "pologne"], "LU": ["luxembourg", "luxemburg", "luxemburgo", "lussemburgo"], "DK": ["danmark", "denmark"],
    "SE": ["sverige", "sweden"], "FI": ["suomi", "finland"], "IE": ["ireland", "éire"],
    "CZ": ["česko", "czechia", "czech republic"], "AT_": [],
}
DMY_NUMERIC = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:[ ,T]+(\d{1,2})[:.h](\d{2})(?::(\d{2}))?)?")
ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?")
TIME = re.compile(r"\b\d{1,2}[:.h]\d{2}\b")


def tz_for(country: str | None) -> ZoneInfo:
    return ZoneInfo(COUNTRY_TZ.get((country or "DE").upper(), "Europe/Berlin"))


def clean_text(s) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


def _strip(s: str) -> str:
    s = re.sub(r"\b(uhr|h|heures?|horas?|uur|godz\.?)\b", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s.replace(",", " ")).strip()


def _parse(s: str, country: str | None) -> tuple[datetime | None, bool, bool]:
    """→ (datetime, has_time, has_offset)."""
    s = str(s).strip()
    if not s or not re.search(r"\d", s):
        return None, False, False
    m = ISO.match(s)
    if m:
        y, mo, d, hh, mm, ss, off = m.groups()
        try:
            if hh is None:
                return datetime(int(y), int(mo), int(d)), False, False
            dt = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
            if off:
                return datetime.fromisoformat(s[: m.end()].replace("Z", "+00:00").replace(" ", "T")), True, True
            return dt, True, False
        except ValueError:
            return None, False, False
    m = DMY_NUMERIC.match(s)
    if m:
        d, mo, y, hh, mm, ss = m.groups()
        try:
            if hh is None:
                return datetime(int(y), int(mo), int(d)), False, False
            return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0)), True, False
        except ValueError:
            return None, False, False
    lang = COUNTRY_LANG.get((country or "").upper())
    dt = dateparser.parse(_strip(s), languages=[lang, "en"] if lang else None,
                          settings={"DATE_ORDER": "DMY", "PREFER_DAY_OF_MONTH": "first",
                                    "REQUIRE_PARTS": ["day", "month", "year"]})
    if dt is None:
        return None, False, False
    return dt, bool(TIME.search(s)), dt.tzinfo is not None


def parse_date(s, country: str | None = None) -> date | None:
    if s is None:
        return None
    dt, _, _ = _parse(s, country)
    return dt.date() if dt else None


def parse_deadline(s, country: str | None = None) -> datetime | None:
    """Date-time with offset. Naive values are local time of the portal's country. Date-only → None."""
    if s is None:
        return None
    dt, has_time, has_off = _parse(s, country)
    if dt is None or not has_time:
        return None
    if has_off and dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=tz_for(country))


def parse_cpv(v) -> list[str]:
    items = v if isinstance(v, list) else [v]
    out: list[str] = []
    for item in items:
        for code in re.findall(r"(?<!\d)(\d{8})(?:-\d)?(?!\d)", str(item or "")):
            if code not in out:
                out.append(code)
    return out


PROCEDURE_KEYS = [
    (ProcedureType.restricted, ["nicht offen", "nichtoffen", "restricted", "restreint", "restringido", "niet-openbaar",
                                "niet openbaar", "niet-openbare", "niet openbare", "ristrett", "ograniczon", "beschränkte ausschreibung"]),
    (ProcedureType.negotiated, ["verhandlung", "negotiat", "négocié", "negocie", "negociado", "onderhandel",
                                "negoziat", "negocjac", "freihändig"]),
    (ProcedureType.other, ["wettbewerblicher dialog", "competitive dialogue", "dialogue compétitif", "diálogo competitivo",
                           "concurrentiegerichte dialoog", "innovationspartnerschaft", "innovation partnership",
                           "partenariat d'innovation", "asociación para la innovación", "innovatiepartnerschap"]),
    (ProcedureType.open, ["offen", "open", "ouvert", "abierto", "openbare", "openbaar", "aperta", "aberto",
                          "nieograniczon", "öffentliche ausschreibung"]),
]
NATURE_KEYS = [
    (ContractNature.works, ["bau", "works", "travaux", "obras", "werken", "lavori", "roboty budowlane", "obra"]),
    (ContractNature.services, ["dienst", "service", "servicios", "diensten", "servizi", "usług", "prestation"]),
    (ContractNature.supplies, ["liefer", "suppl", "fourniture", "suministro", "levering", "forniture", "dostaw"]),
]


def map_procedure(s) -> ProcedureType | None:
    t = (clean_text(s) or "").lower()
    for value, keys in PROCEDURE_KEYS:
        if any(k in t for k in keys):
            return value
    return None


def map_nature(s) -> ContractNature | None:
    t = (clean_text(s) or "").lower()
    for value, keys in NATURE_KEYS:
        if any(k in t for k in keys):
            return value
    return None


def map_status(v) -> TenderStatus | None:
    if isinstance(v, bool):
        return TenderStatus.withdrawn if v else None
    t = (clean_text(v) or "").lower()
    if any(k in t for k in ["aufgehoben", "zurückgezogen", "withdrawn", "cancel", "annul", "anulad", "ingetrokken",
                             "geannuleerd", "true"]):
        return TenderStatus.withdrawn
    return None


def parse_country(s) -> str | None:
    t = (clean_text(s) or "")
    if re.fullmatch(r"[A-Za-z]{2}", t):
        return t.upper()
    low = t.lower()
    for code, names in COUNTRY_NAMES.items():
        if len(code) == 2 and any(n in low for n in names):
            return code
    m = re.search(r"\b([A-Z]{2})\b$", t)
    return m.group(1) if m and m.group(1) in COUNTRY_TZ else None


def parse_int(s) -> int | None:
    m = re.search(r"\d+", str(s or "").replace(".", ""))
    return int(m.group()) if m else None
