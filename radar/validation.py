import re
from urllib.parse import urlparse

FIELDS = {
    "phone": "Telefone profissional",
    "legal_name": "Empresa · razão social cadastral",
    "linkedin_person": "LinkedIn da pessoa",
    "linkedin": "LinkedIn da empresa",
    "instagram": "Instagram da empresa",
    "website": "Site",
    "role": "Cargo",
    "decision_role": "Papel na decisão",
    "segment": "Segmento",
    "cnpj": "CNPJ",
    "capital_social": "Capital social",
    "employee_count": "Pessoas na empresa",
    "employee_range": "Faixa de pessoas (estimativa)",
    "cnae": "CNAE",
    "company_size": "Porte cadastral",
    "city": "Cidade",
    "state": "UF",
    "product_fit": "Fit de produto (hipótese revisada)",
}
CORE_FIELDS = tuple(
    k
    for k in FIELDS
    if k
    not in {
        "product_fit",
        "employee_range",
        "linkedin_person",
    }
)
SEGMENTS = (
    "Escritório de projetos",
    "Construtora",
    "Instaladora",
    "Incorporadora",
    "Escritório de gestão",
    "Indústria",
    "Fabricante",
    "Arquitetura",
    "Infraestrutura",
    "Setor público",
    "Outro",
)
DECISION_ROLES = (
    "Decisor confirmado",
    "Provável decisor",
    "Provável influenciador",
    "Influenciador",
    "Usuário",
    "Não confirmado",
)


def normalize_cnpj(value):
    return re.sub(r"[.\s/\-]", "", str(value)).upper()


def valid_cnpj(value):
    value = normalize_cnpj(value)
    if not re.fullmatch(r"[A-Z0-9]{12}[0-9]{2}", value) or len(set(value)) == 1:
        return False
    base = [ord(char) - 48 for char in value]
    for end, weights in (
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ):
        remainder = sum(a * b for a, b in zip(base[:end], weights, strict=True)) % 11
        if base[end] != (0 if remainder < 2 else 11 - remainder):
            return False
    return True


def normalize_email(value):
    value = str(value or "").strip().lower()
    if not value:
        return ""
    if value.count("@") != 1:
        raise ValueError("E-mail inválido.")
    local, domain = value.split("@")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("E-mail inválido.") from exc
    labels = domain.split(".")
    if (
        not 1 <= len(local) <= 64
        or len(local + "@" + domain) > 254
        or not re.fullmatch(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+", local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in labels)
    ):
        raise ValueError("E-mail inválido.")
    return local + "@" + domain


def public_url(value):
    value = str(value).strip()
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    parsed = urlparse(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 80, 443)
    ):
        raise ValueError("Informe uma URL pública HTTP ou HTTPS, sem credenciais.")
    return value


def validate_value(field, value):
    if field not in FIELDS:
        raise ValueError("Campo fora do padrão do Radar.")
    if value is None or isinstance(value, (dict, list, bool)) or str(value).strip() == "":
        raise ValueError("O Radar não aprova valores vazios.")
    if field in ("employee_count", "capital_social"):
        number = float(value)
        if not 0 <= number <= 1e15 or (field == "employee_count" and not number.is_integer()):
            raise ValueError("Informe um número válido e não negativo.")
        return int(number) if field == "employee_count" else number
    value = str(value).strip()
    if len(value) > 500:
        raise ValueError("Valor muito longo (máximo 500 caracteres).")
    if field == "cnpj":
        if not valid_cnpj(value):
            raise ValueError("CNPJ inválido. Confira os dígitos verificadores.")
        return normalize_cnpj(value)
    if field == "employee_range":
        value = value.replace("-", "–")
        if not re.fullmatch(r"(?:[0-9]+\+|[0-9]+–[0-9]+)", value):
            raise ValueError("Use uma faixa como 201–500 ou 300+, sem converter em número exato.")
        bounds = [int(part) for part in value.rstrip("+").split("–")]
        if any(bound > 1000000 for bound in bounds) or bounds != sorted(bounds):
            raise ValueError("Faixa de funcionários inválida.")
    if field in ("linkedin", "linkedin_person", "instagram", "website"):
        value = public_url(value)
        parsed = urlparse(value)
        host = parsed.hostname.lower().removeprefix("www.")
        if field == "linkedin_person":
            if not (host == "linkedin.com" or host.endswith(".linkedin.com")) or not re.fullmatch(
                r"/in/[A-Za-z0-9%_-]+/?", parsed.path
            ):
                raise ValueError("Use o perfil profissional da pessoa no LinkedIn (/in/).")
            return "https://www.linkedin.com" + parsed.path.rstrip("/")
        if field == "linkedin" and (
            host != "linkedin.com" or not parsed.path.startswith("/company/")
        ):
            raise ValueError("Use a página da empresa no LinkedIn (/company/).")
        if field == "instagram" and (host != "instagram.com" or len(parsed.path.strip("/")) == 0):
            raise ValueError("Use a página empresarial no Instagram.")
    if field == "phone":
        digits = re.sub(r"\D", "", value)
        if len(digits) in (10, 11):
            digits = "55" + digits
        ddds = {
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            21,
            22,
            24,
            27,
            28,
            31,
            32,
            33,
            34,
            35,
            37,
            38,
            41,
            42,
            43,
            44,
            45,
            46,
            47,
            48,
            49,
            51,
            53,
            54,
            55,
            61,
            62,
            63,
            64,
            65,
            66,
            67,
            68,
            69,
            71,
            73,
            74,
            75,
            77,
            79,
            81,
            82,
            83,
            84,
            85,
            86,
            87,
            88,
            89,
            91,
            92,
            93,
            94,
            95,
            96,
            97,
            98,
            99,
        }
        if (
            not digits.startswith("55")
            or len(digits) not in (12, 13)
            or int(digits[2:4]) not in ddds
            or (len(digits) == 13 and digits[4] != "9")
            or (len(digits) == 12 and digits[4] not in "2345")
        ):
            raise ValueError("Telefone brasileiro inválido. Use +55 (DDD) número.")
        return f"+55 ({digits[2:4]}) {digits[4:-4]}-{digits[-4:]}"
    if field == "decision_role" and value not in DECISION_ROLES:
        raise ValueError("Escolha um papel na decisão do padrão.")
    return value


def clean_data(data):
    return {
        key: validate_value(key, value)
        for key, value in data.items()
        if value is not None and str(value).strip() != ""
    }


def completeness(data):
    return round(
        100
        * sum(data.get(key) is not None and data.get(key) != "" for key in CORE_FIELDS)
        / len(CORE_FIELDS)
    )


def csv_safe(value):
    value = str(value if value is not None else "")
    return (
        "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value
    )
