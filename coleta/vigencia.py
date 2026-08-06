"""
Prazo de vigência das MPs de crédito extraordinário.

A tabela de propostas do Congresso não traz a vigência. Aqui ela é obtida em
quatro camadas, da mais confiável para a menos:

1. `config/vigencia_manual.csv` — datas que você tenha conferido à mão. Vence
   sempre.
2. **Prazos oficiais da página da MP** (`config/anexos.json`): o Congresso
   publica ali a janela de deliberação, que é a vigência. É a fonte boa.
3. Dados Abertos do Senado — data de apresentação, para calcular.
4. Início da janela de emendas raspada da tabela, como último recurso.

O cálculo do art. 62 (60 dias prorrogáveis por 60, suspenso no recesso) ficou
como fallback, e vale saber por quê: para a MPV 1378/2026, publicada em 21/07 e
portanto dentro do recesso de julho, o Congresso fixou a deliberação em 21/07 a
18/09 — sessenta dias corridos, sem suspensão —, enquanto suspendeu o prazo de
emendas. A prática é menos direta que o texto constitucional, então a data
oficial manda.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

API_MATERIA = "https://legis.senado.leg.br/dadosabertos/materia/{codigo}"
HEADERS = {"Accept": "application/json",
           "User-Agent": "painel-creditos-extraordinarios/1.0"}

# Recessos do Congresso Nacional (art. 57 da Constituição).
RECESSOS = [((12, 23), (12, 31)), ((1, 1), (2, 1)), ((7, 18), (7, 31))]

PRAZO_INICIAL = 60
PRAZO_PRORROGADO = 120


def em_recesso(d: date) -> bool:
    for (mi, di), (mf, df) in RECESSOS:
        if (d.month, d.day) >= (mi, di) and (d.month, d.day) <= (mf, df):
            return True
    return False


def somar_dias_uteis_de_vigencia(inicio: date, dias: int) -> date:
    """Data em que se completa o n-ésimo dia de vigência, pulando recesso.

    O dia da publicação conta como dia 1.
    """
    contados = 0
    d = inicio
    limite = inicio + timedelta(days=dias + 400)
    while d <= limite:
        if not em_recesso(d):
            contados += 1
            if contados >= dias:
                return d
        d += timedelta(days=1)
    return d


def _busca_chave(obj, chave: str):
    """Procura recursivamente uma chave no JSON do Senado."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == chave.lower() and isinstance(v, str) and v.strip():
                return v.strip()
            achado = _busca_chave(v, chave)
            if achado:
                return achado
    elif isinstance(obj, list):
        for item in obj:
            achado = _busca_chave(item, chave)
            if achado:
                return achado
    return None


def data_apresentacao_senado(codigo: str) -> str | None:
    try:
        r = requests.get(API_MATERIA.format(codigo=codigo), headers=HEADERS, timeout=45)
        if r.status_code != 200:
            return None
        dados = r.json()
    except Exception:  # noqa: BLE001
        return None
    for chave in ("DataApresentacao", "DataLeitura", "DataPublicacao"):
        valor = _busca_chave(dados, chave)
        if valor and len(valor) >= 10:
            return valor[:10]
    return None


def carregar_oficiais(caminho: Path) -> dict[str, dict]:
    """Prazos oficiais colhidos da página da MP por coleta/anexos.py."""
    if not caminho.exists():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8"))


def carregar_manual(caminho: Path) -> dict[str, dict]:
    if not caminho.exists():
        return {}
    manual: dict[str, dict] = {}
    with caminho.open(encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            ident = (linha.get("identificacao") or "").strip()
            if ident:
                manual[ident] = {k: (v or "").strip() for k, v in linha.items()}
    return manual


def enriquecer(propostas: list[dict], manual: dict[str, dict],
               oficiais: dict[str, dict] | None = None) -> list[dict]:
    oficiais = oficiais or {}
    for p in propostas:
        if not p.get("tipo", "").lower().startswith("crédito extraordin"):
            continue

        ident = p["identificacao"]
        registro = manual.get(ident, {})
        oficial = oficiais.get(ident, {})
        fonte = None
        publicacao = registro.get("publicacao") or None
        if publicacao:
            fonte = "manual"

        # a janela de emendas oficial da página da MP corrige a da tabela
        if oficial.get("emendas_inicio_oficial"):
            p["emendas_inicio"] = oficial["emendas_inicio_oficial"]
        if oficial.get("emendas_fim_oficial"):
            p["emendas_fim"] = oficial["emendas_fim_oficial"]

        if not publicacao and oficial.get("publicacao_dou"):
            publicacao, fonte = oficial["publicacao_dou"], "oficial (página da MP)"
        # o bloco "Prazos abertos" não traz a data do DOU, mas a deliberação
        # começa no dia da publicação
        if not publicacao and oficial.get("deliberacao_inicio"):
            publicacao, fonte = oficial["deliberacao_inicio"], "oficial (início da deliberação)"

        if not publicacao and p.get("codigo_materia"):
            publicacao = data_apresentacao_senado(p["codigo_materia"])
            if publicacao:
                fonte = "senado"
            time.sleep(0.4)

        if not publicacao:
            publicacao = p.get("emendas_inicio")
            if publicacao:
                fonte = "estimado (início do prazo de emendas)"

        p["publicacao"] = publicacao
        p["vigencia_fonte"] = fonte

        if registro.get("vigencia_fim"):
            p["vigencia_60"] = registro.get("vigencia_60") or None
            p["vigencia_fim"] = registro["vigencia_fim"]
            p["vigencia_fonte"] = "manual"
        elif oficial.get("deliberacao_fim"):
            # A janela publicada é a do período corrente. A prorrogação por mais
            # 60 dias é automática (art. 62, § 7º), então a data em que a MP de
            # fato caduca é o fim do segundo período — antes disso, o dia 60 é
            # só o momento em que a prorrogação acontece.
            fim_periodo = date.fromisoformat(oficial["deliberacao_fim"])
            if oficial.get("prorrogada"):
                p["vigencia_60"] = (
                    date.fromisoformat(publicacao) + timedelta(days=59)
                ).isoformat() if publicacao else None
                p["vigencia_fim"] = oficial["deliberacao_fim"]
                p["vigencia_fonte"] = "oficial (prorrogada)"
            else:
                p["vigencia_60"] = oficial["deliberacao_fim"]
                p["vigencia_fim"] = (fim_periodo + timedelta(days=60)).isoformat()
                p["vigencia_fonte"] = "oficial + prorrogação projetada"
            p["prorrogada"] = bool(oficial.get("prorrogada"))
            p["ato_prorrogacao"] = oficial.get("ato_prorrogacao")
            p["fim_periodo_atual"] = oficial["deliberacao_fim"]
            p["inicio_periodo_atual"] = oficial.get("deliberacao_inicio")
            p["urgencia"] = oficial.get("urgencia")
        elif publicacao:
            inicio = date.fromisoformat(publicacao)
            p["vigencia_60"] = somar_dias_uteis_de_vigencia(inicio, PRAZO_INICIAL).isoformat()
            p["vigencia_fim"] = somar_dias_uteis_de_vigencia(inicio, PRAZO_PRORROGADO).isoformat()
        else:
            p["vigencia_60"] = None
            p["vigencia_fim"] = None
    return propostas


def main() -> None:
    entrada = Path("dados/congresso.json")
    propostas = json.loads(entrada.read_text(encoding="utf-8"))
    manual = carregar_manual(Path("config/vigencia_manual.csv"))
    oficiais = carregar_oficiais(Path("config/anexos.json"))
    propostas = enriquecer(propostas, manual, oficiais)
    entrada.write_text(json.dumps(propostas, ensure_ascii=False, indent=2), encoding="utf-8")
    com_data = sum(1 for p in propostas if p.get("vigencia_fim"))
    print(f"vigência calculada para {com_data} matérias", file=sys.stderr)


if __name__ == "__main__":
    main()
