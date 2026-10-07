"""Dados exclusivamente fictícios. Nenhuma pesquisa remota é executada neste modo."""

from radar.service import capture, enqueue
from radar.sources import suggestion

EXAMPLES = [
    (
        "Aurora Projetos",
        "Marina Costa",
        "aurora",
        "Escritório de projetos",
        {"role": "Engenheira civil"},
        {
            "business_model": "engineering_office",
            "building_design": True,
            "disciplines": ["structure", "installations"],
            "designers": 8,
            "pain": "management",
            "sponsor": True,
            "assisted_capacity": True,
        },
    ),
    (
        "Vértice Construções",
        "Rafael Mendes",
        "vertice",
        "Construtora",
        {},
        {
            "business_model": "builder",
            "building_work": True,
            "absorbs_method": True,
            "uses_bim": True,
            "sector_autonomy": True,
            "pain": "quantities",
        },
    ),
    (
        "Prumo Engenharia",
        "Lucas Nunes",
        "prumo",
        "Escritório de projetos",
        {"website": "https://prumo.example"},
        {
            "business_model": "engineering_office",
            "building_design": True,
            "disciplines": ["structure"],
            "designers": 2,
            "pain": "engineering",
        },
    ),
    (
        "Horizonte Obras",
        "Camila Almeida",
        "horizonte",
        "Construtora",
        {"role": "Sócia"},
        {"business_model": "builder", "building_work": True, "absorbs_method": False},
    ),
    (
        "Nexo Gestão BIM",
        "Pedro Santos",
        "nexo",
        "Escritório de gestão",
        {},
        {"business_model": "management_service", "service": "budget"},
    ),
    (
        "Átrio Projetos",
        "Beatriz Lima",
        "atrio",
        "Escritório de projetos",
        {},
        {
            "business_model": "engineering_office",
            "building_design": True,
            "disciplines": ["installations"],
            "pain": "engineering",
        },
    ),
    (
        "Lume Incorporadora",
        "Diego Martins",
        "lume",
        "Incorporadora",
        {},
        {
            "business_model": "developer",
            "building_work": True,
            "absorbs_method": True,
            "uses_bim": False,
        },
    ),
    (
        "Plano Norte",
        "Juliana Rocha",
        "planonorte",
        "Escritório de projetos",
        {},
        {
            "business_model": "engineering_office",
            "building_design": True,
            "disciplines": ["structure", "installations"],
            "designers": 22,
            "pain": "both",
            "sponsor": True,
            "assisted_capacity": True,
            "maturity": [3, 3, 2, 3, 2, 3, 2],
        },
    ),
]


def seed(conn):
    ids = []
    for company, name, slug, segment, data, qualification in EXAMPLES:
        result = capture(
            conn,
            {
                "name": name,
                "company": company,
                "email": f"contato@{slug}.example",
                "data": {"segment": segment, **data},
                "qualification": qualification,
            },
            "demo:v1:" + slug,
            origin="demonstração",
            demo=True,
        )
        ids.append(result["id"])
    enqueue(conn, ids[:5])
    return {
        "count": len(ids),
        "message": "Oito contatos fictícios disponíveis. Pesquisa simulada enfileirada para cinco.",
    }


def demo_proposals(lead):
    slug = lead["email"].split("@")[-1].split(".")[0]
    index = next((i for i, item in enumerate(EXAMPLES) if item[2] == slug), 0)
    values = {
        "website": f"https://{slug}.example",
        "role": [
            "Coordenadora de projetos",
            "Diretor técnico",
            "Engenheiro projetista",
            "Sócia diretora",
            "Engenheiro de custos",
        ][index % 5],
        "employee_count": [12, 86, 3, 5, 9][index % 5],
        "capital_social": [180000, 1200000, 60000, 95000, 160000][index % 5],
        "linkedin": f"https://www.linkedin.com/company/radar-demo-{slug}",
        "instagram": f"https://www.instagram.com/radar_demo_{slug}/",
    }
    return [
        suggestion(
            field,
            value,
            "demo",
            f"https://{slug}.example/evidencias",
            f"Exemplo fictício do hackathon: {field} = {value}. Não representa pesquisa pública nem empresa real.",
            85 if field != "role" else 75,
        )
        for field, value in values.items()
    ]
