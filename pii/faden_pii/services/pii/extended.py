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
    "NATIONAL_ID": (
        r"national[ _-]?id|id[ _-]?card(?:[ _-]?(?:number|num))?|identity[ _-]?(?:card|number|document)"
        r"|personalausweis(?:nummer)?|ausweisnummer|identit[aä]tskarte"
        r"|carte (?:nationale )?d['’]identit[eé]|num[eé]ro d['’](?:identit[eé]|identification)"
        r"|documento (?:nacional )?de identidad|n[uú]mero de identificaci[oó]n(?: nacional| oficial)?"
        r"|identificaci[oó]n (?:oficial|nacional)|dni|nie|\bid\b"
    ),
    "PASSPORT": (
        r"passport(?:[ _-]?(?:number|no\.?))?|reisepass(?:nummer)?|passnummer"
        r"|(?:num[eé]ro de )?passeport|(?:n[uú]mero de )?pasaporte"
    ),
    "DRIVER_LICENSE": (
        r"driv(?:er['’]?s?|ing)[ _-]?licen[cs]e(?:[ _-]?(?:number|no\.?))?|licen[cs]e[ _-]?(?:number|no\.?)"
        r"|f[uü]hrerschein(?:nummer)?|(?:num[eé]ro de )?permis(?: de conduire)?"
        r"|(?:(?:n[uú]mero de )?(?:permiso|carn[eé]|licencia) de conducir)"
    ),
    "TAX_ID": (
        r"tax[ _-]?(?:id(?:entification)?|number)(?:[ _-]?number)?|steuer(?:identifikationsnummer|nummer|[ _-]?id)"
        r"|identifiant fiscal|num[eé]ro (?:fiscal|de taxe|d['’]imp[oô]t)"
        r"|(?:n[uú]mero de )?identificaci[oó]n fiscal|nif|n[uú]mero fiscal"
    ),
    "SOCIAL_SECURITY_NUMBER": (
        r"social[ _-]?security(?:[ _-]?(?:number|no\.?))?|(?:national )?insurance number|ssn"
        r"|sozialversicherungs(?:nummer|[ _-]?nr\.?)|rentenversicherungsnummer"
        r"|num[eé]ro (?:de s[eé]curit[eé] sociale|d['’]assurance(?: sociale)?)"
        r"|(?:n[uú]mero de (?:la )?)?(?:seguridad|seguro) social"
    ),
    "CREDIT_CARD": (
        r"credit[ _-]?card(?:[ _-]?(?:number|no\.?))?|card[ _-]?(?:number|ending (?:in|with))"
        r"|kreditkarte(?:nnummer)?|kartennummer|carte (?:de cr[eé]dit|bancaire)|num[eé]ro de carte"
        r"|tarjeta(?: de cr[eé]dito)?|n[uú]mero de tarjeta"
    ),
    "PHONE_NUMBER": (
        r"(?:tele)?phone(?:[ _-]?(?:number|no\.?))?|tel(?:efon(?:nummer|isch(?: unter)?)?|[eé]phone|[eé]fono)?"
        r"|n[uú]mero de (?:tel[eé]fono|contacto)|num[eé]ro de t[eé]l[eé]phone"
    ),
    "AGE": (
        r"age(?:\s+of\s+the\s+\w+)?|aged|alter|im alter von"
        r"|[aâ]ge|[aâ]g[eé]\(?e?\)?\s+de|de plus de|edad|mayor(?:es)? de"
    ),
    "GENDER": (
        r"gender(?:[ _-]?identity)?|biological[ _-]?sex|sex|geschlecht(?:sidentit[aä]t)?"
        r"|identit[eé] de genre|genre|sexe(?: biologique)?|identidad de g[eé]nero|g[eé]nero|sexo(?: biol[oó]gico)?"
    ),
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
EXTENDED_ENTITIES = tuple(
    k for k in FIELDS if k not in {"CREDIT_CARD", "PHONE_NUMBER"}
) + ("DATE_TIME", "TITLE")


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


_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Januar|Februar|M[aä]rz|Mai|Juni|Juli|Oktober|Dezember"
    r"|janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre"
    r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|M[aä]r|Okt|Dez|janv|f[eé]vr|avr|juil|sept|d[eé]c|ene|abr|ago|dic)\.?"
)
_DATE = re.compile(
    r"(?<!\w)(?:"
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})"
    r"|\d{1,2}(?:st|nd|rd|th|er|e|[oºª]|\.)?\s+(?:de\s+|d['’])?"
    + _MONTHS
    + r"(?:\s+de)?,?\s+\d{2,4}"
    r"|" + _MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th|er|e|[oºª]|\.)?,?\s+\d{2,4}"
    r"|" + _MONTHS + r"[/.-]\d{2,4}"
    r")(?!\w)",
    re.I,
)
_TITLE = re.compile(
    r"(?<!\w)(?:"
    r"(?:Mr|Mister|Mrs|Ms|Miss|Mx|Dr|Prof|Herr|Hr|Frau|Mme|Mlle|Mademoiselle|Monsieur|Madame|Ma[iî]tre|Mtre|Mstr|Se[nñ]or(?:a|ita)?|Sr|Sra|Srta)\.?"
    r"|(?:(?:Doktorin|Docteure|Professorin|Professeure|Profesora|Meister|Sen|Senator(?:a|in)?|S[eé]nateur|Senador|Mayor(?:ess)?|Mairesse|B[uü]rgermeister(?:in)?|Bgm|President(?:e|a)?|Pr[eé]sident(?:e)?|Premier|Ministro|Diputad[oa]|Director(?:a)?|Dir|Ing|Mag|Kpt|Notaire|Abogad[oa]|Embajador(?:a)?|\bM)\.?)(?=\s+[A-ZÄÖÜÉÈÊÀÁÍÓÚÑ])"
    r")(?!\w)"
)
_AGE_POST = re.compile(
    r"(?<!\w)(?P<value>\d{1,3})(?:\s*[- ]\s*|\s+)(?:years?\s+old|yo\b|yr[s.]?\s+old|year[- ]old|jahre\s+alt|j[aä]hrig(?:e[nrs]?)?|ans\b|a[nñ]os(?:\s+de\s+edad)?\b)",
    re.I,
)
_GENDER_IDENTITY = re.compile(
    r"(?<!\w)(?P<value>agender|agenre|ag[eé]nero|genderqueer|genre[ -]?queer|geschlechterqueer|genderfluid|genre[ -]?fluide|geschlechtsfluid|g[eé]nero[ -]?fluido|bigender|bigenre|big[eé]nero|transgender|transgenre|transg[eé]nero|two[- ]?spirit|deux[- ]?esprits|zwei[- ]?geister|dos[ -]?esp[ií]ritus|non[- ]?binary|non[- ]?binaire|nicht[- ]?bin[aä]r|no[- ]?binario|androgyn(?:ous|e)?|andr[oó]gino)(?!\w)",
    re.I,
)


class ExtendedFieldsRecognizer(EntityRecognizer):
    def __init__(self, language):
        super().__init__(
            supported_entities=list(EXTENDED_ENTITIES)
            + ["CREDIT_CARD", "PHONE_NUMBER"],
            supported_language=language,
            name="ExtendedFields-v1",
        )
        self.patterns = []
        for entity, keys in FIELDS.items():
            if entity in IDENTIFIERS:
                value = r"(?:\d(?:[ .-]?\d){4,30}|[A-Z0-9][A-Z0-9._/-]{3,62}[A-Z0-9])"
                separator = r"(?:\s*\([^)\n]{1,25}\))?\s*(?::|=|\(|\b(?:is|ist|est|es|n[oº°.]|nr\.?|#)\b)?\s*"
            elif entity == "CREDIT_CARD":
                value = r"(?:\d[ -]?){12,19}\d"
                separator = r"(?:\s*\([^)\n]{1,25}\))?\s*(?::|=|\(|\b(?:is|ist|est|es|n[oº°.]|nr\.?|#)\b)?\s*"
            elif entity == "PHONE_NUMBER":
                value = r"\+?[\d][\d ./()-]{6,22}\d"
                separator = r"(?:\s*\([^)\n]{1,25}\))?\s*(?::|=|\(|\b(?:is|ist|est|es|n[oº°.]|nr\.?|#)\b)?\s*"
            elif entity == "POSTAL_CODE":
                value = r"(?:\d{5}-\d{4}|\d{4,6}|[A-Z]\d[A-Z][ -]?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})"
                separator = (
                    r"(?:\s*\([^)\n]{1,25}\))?\s*(?::|=|\(|\b(?:is|ist|est|es)\b)\s*"
                )
            elif entity == "AGE":
                value = r"\d{1,3}"
                separator = (
                    r"(?:\s*\([^)\n]{1,25}\))?\s*(?::|=|\(|\b(?:is|ist|est|es)\b)?\s*"
                )
            elif entity == "GENDER":
                value = (
                    r"(?:female|male|woman|man|non[- ]?binary|non[- ]?binaire|nicht[- ]?bin[aä]r|no[- ]?binario"
                    r"|divers|weiblich|m[aä]nnlich|f[eé]minin|masculin|femme|homme|mujer|hombre|femenino|masculino"
                    r"|agender|agenre|ag[eé]nero|genderqueer|genre[ -]?queer|geschlechterqueer|genderfluid|genre[ -]?fluide"
                    r"|geschlechtsfluid|g[eé]nero[ -]?fluido|bigender|bigenre|big[eé]nero|transgender|transgenre|transg[eé]nero"
                    r"|two[- ]?spirit|deux[- ]?esprits|zwei[- ]?geister|dos[ -]?esp[ií]ritus|androgyn(?:ous|e)?|andr[oó]gino"
                    r"|other|autre|andere|otro|not[ -]?specified|nicht[ -]?spezifiziert|non[ -]?sp[eé]cifi[eé]|no[ -]?especificado|[mfwox])"
                )
                separator = (
                    r"(?:\s*\([^)\n]{1,25}\))?\s*(?::|=|\(|\b(?:is|ist|est|es)\b)\s*"
                )
            else:
                value = r"[^\n\r;\"{}]+"
                separator = r"\s*(?::|=)\s*"
            pattern = re.compile(
                r"(?<!\w)(?:"
                + keys
                + r")[\"']?"
                + separator
                + r"[\"']?(?P<value>"
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
        if "AGE" in requested:
            for match in _AGE_POST.finditer(text):
                start, end = match.span("value")
                if int(text[start:end]) <= 125:
                    results.append(RecognizerResult("AGE", start, end, 0.85))
        if "GENDER" in requested:
            for match in _GENDER_IDENTITY.finditer(text):
                start, end = match.span("value")
                results.append(RecognizerResult("GENDER", start, end, 0.85))
        for entity, pattern in (("DATE_TIME", _DATE), ("TITLE", _TITLE)):
            if entity in requested:
                results.extend(
                    RecognizerResult(entity, m.start(), m.end(), 0.8)
                    for m in pattern.finditer(text)
                )
        return results
