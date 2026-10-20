"""Opt-in local rules for demographic, document and sensitive declared fields.

These rules complement the name/address model without enlarging its prompt or
loading another model. Sensitive categories recognize explicit declarations,
not arbitrary medical, political or religious discussion.
"""

import re

from presidio_analyzer import EntityRecognizer, RecognizerResult

REVISION = "extended-fields-v1"

# A shared multilingual field vocabulary is intentional: imported forms often
# contain English field names even when the surrounding text is not English.
FIELDS = {
    "POSTAL_CODE": r"postal[ _-]?code|postcode|zip(?:[ _-]?code)?|postleitzahl|plz|code postal|c[oó]digo postal",
    "NATIONAL_ID": r"national[ _-]?id|id[ _-]?card(?:[ _-]?(?:number|num))?|identity[ _-]?(?:card|number)|personalausweis(?:nummer)?|ausweisnummer|carte d['’]identit[eé]|num[eé]ro d['’]identit[eé]|documento de identidad|dni|nie",
    "PASSPORT": r"passport(?:[ _-]?(?:number|no\.?))?|reisepass(?:nummer)?|passnummer|passeport|pasaporte",
    "DRIVER_LICENSE": r"driv(?:er['’]?s?|ing)[ _-]?licen[cs]e(?:[ _-]?(?:number|no\.?))?|f[uü]hrerschein(?:nummer)?|permis de conduire|(?:permiso|carn[eé]) de conducir",
    "TAX_ID": r"tax[ _-]?(?:id(?:entification)?|number)(?:[ _-]?number)?|steuer(?:identifikationsnummer|nummer|[ _-]?id)|identifiant fiscal|num[eé]ro fiscal|n[uú]mero de identificaci[oó]n fiscal|nif|n[uú]mero fiscal",
    "SOCIAL_SECURITY_NUMBER": r"social[ _-]?security(?:[ _-]?(?:number|no\.?))?|ssn|sozialversicherungsnummer|rentenversicherungsnummer|num[eé]ro de s[eé]curit[eé] sociale|n[uú]mero de (?:la )?seguridad social",
    "AGE": r"age|alter|[aâ]ge|edad",
    "GENDER": r"gender|sex|geschlecht|genre|sexe|g[eé]nero|sexo",
    "HEALTH_DATA": r"diagnosis|medical[ _-]?condition|health[ _-]?(?:data|status)|diagnose|erkrankung|gesundheitszustand|diagnostic|[eé]tat de sant[eé]|diagn[oó]stico|enfermedad|estado de salud",
    "ETHNICITY": r"ethnicity|racial origin|ethnic origin|ethnische herkunft|ethnie|origine ethnique|origen [eé]tnico|etnia",
    "POLITICAL_OPINION": r"political[ _-]?(?:opinion|affiliation)|politische (?:meinung|zugeh[oö]rigkeit)|opinion politique|affiliation politique|opini[oó]n pol[ií]tica|afiliaci[oó]n pol[ií]tica",
    "RELIGION": r"religion|religious belief|philosophical belief|weltanschauung|religi[oö]se [uü]berzeugung|croyance religieuse|conviction philosophique|religi[oó]n|creencia religiosa|convicci[oó]n filos[oó]fica",
    "TRADE_UNION": r"trade[ _-]?union(?:[ _-]?membership)?|union membership|gewerkschaft(?:szugeh[oö]rigkeit)?|appartenance syndicale|syndicat|afiliaci[oó]n sindical|sindicato",
    "GENETIC_DATA": r"genetic[ _-]?(?:data|result)|genetische (?:daten|befunde)|donn[eé]es g[eé]n[eé]tiques|datos gen[eé]ticos",
    "BIOMETRIC_DATA": r"biometric[ _-]?(?:data|identifier|template)|biometrische (?:daten|kennung)|donn[eé]es biom[eé]triques|datos biom[eé]tricos",
    "SEXUAL_ORIENTATION": r"sexual[ _-]?orientation|sexuelle orientierung|orientation sexuelle|orientaci[oó]n sexual",
    "SEX_LIFE": r"sex[ _-]?life|sexual[ _-]?history|sexualleben|vie sexuelle|vida sexual",
    "CRIMINAL_RECORD": r"criminal[ _-]?record|criminal conviction|vorstrafe[n]?|strafrechtliche verurteilung|casier judiciaire|condamnation p[eé]nale|antecedentes penales|condena penal",
    "ONLINE_IDENTIFIER": r"device[ _-]?id|cookie[ _-]?id|advertising[ _-]?id|ger[aä]tekennung|identifiant de l['’]appareil|identificador del dispositivo",
}
IDENTIFIERS = {
    "NATIONAL_ID",
    "PASSPORT",
    "DRIVER_LICENSE",
    "TAX_ID",
    "SOCIAL_SECURITY_NUMBER",
    "ONLINE_IDENTIFIER",
}
EXTENDED_ENTITIES = tuple(FIELDS) + ("DATE_TIME", "TITLE")


def declared_entity(key):
    if not isinstance(key, str):
        return None
    normalized = key.replace("_", " ").replace("-", " ")
    if re.fullmatch(
        r"date of birth|birth date|dob|geburtsdatum|date de naissance|fecha de nacimiento",
        normalized,
        re.I,
    ):
        return "DATE_TIME"
    return next(
        (
            entity
            for entity, keys in FIELDS.items()
            if re.fullmatch(keys, normalized, re.I)
        ),
        None,
    )


_DATE = re.compile(
    r"(?<!\w)(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:T\d{2}:\d{2}:\d{2})?"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{4}"
    r"|\d{1,2}\.?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Januar|Februar|M[aä]rz|Mai|Juni|Juli|Oktober|Dezember"
    r"|janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre"
    r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)(?:\s+de)?\s+\d{4}"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})(?!\w)",
    re.I,
)
_TITLE = re.compile(
    r"(?<!\w)(?:Mr|Mrs|Ms|Miss|Dr|Prof|Herr|Frau|Mme|Mlle|Monsieur|Madame|Se[nñ]or(?:a|ita)?|Sr|Sra|Srta)\.?(?!\w)",
    re.I,
)


class ExtendedFieldsRecognizer(EntityRecognizer):
    def __init__(self, language):
        super().__init__(
            supported_entities=list(EXTENDED_ENTITIES),
            supported_language=language,
            name="ExtendedFields-v1",
        )
        self.patterns = []
        for entity, keys in FIELDS.items():
            if entity in IDENTIFIERS:
                value = r"(?:\d(?:[ -]?\d){4,30}|[A-Z0-9][A-Z0-9_/-]{3,62}[A-Z0-9])"
            elif entity == "POSTAL_CODE":
                value = r"(?:\d{5}-\d{4}|\d{4,6}|[A-Z]\d[A-Z][ -]?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})"
            elif entity == "AGE":
                value = r"\d{1,3}"
            elif entity == "GENDER":
                value = r"(?:female|male|woman|man|non[- ]binary|divers|weiblich|m[aä]nnlich|f[eé]minin|masculin|femme|homme|mujer|hombre|femenino|masculino|no binario|[mfwx])"
            else:
                value = r"[^\n\r;\"{}]+"
            # Colons/equals or explicit copulas distinguish a declared value
            # from headings, requests for a field and general subject matter.
            separator = (
                r"(?::|=|\bis\b|\bist\b|\best\b|\bes\b)"
                if entity in IDENTIFIERS | {"AGE", "GENDER", "POSTAL_CODE"}
                else r"(?::|=)"
            )
            pattern = re.compile(
                r"(?<!\w)(?:"
                + keys
                + r")[\"']?\s*"
                + separator
                + r"\s*[\"']?(?P<value>"
                + value
                + r")(?!\w)",
                re.I,
            )
            self.patterns.append((entity, pattern))

    def load(self):
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        requested = set(entities)
        results = []
        for entity, pattern in self.patterns:
            if entity not in requested:
                continue
            for match in pattern.finditer(text):
                start, end = match.span("value")
                value = text[start:end].rstrip()
                end = start + len(value)
                if entity in IDENTIFIERS and not any(c.isdigit() for c in value):
                    continue
                if entity == "AGE" and int(value) > 125:
                    continue
                if entity == "POSTAL_CODE" and not any(c.isdigit() for c in value):
                    continue
                if value:
                    results.append(RecognizerResult(entity, start, end, 0.85))
        for entity, pattern in (("DATE_TIME", _DATE), ("TITLE", _TITLE)):
            if entity in requested:
                results.extend(
                    RecognizerResult(entity, m.start(), m.end(), 0.8)
                    for m in pattern.finditer(text)
                )
        return results
