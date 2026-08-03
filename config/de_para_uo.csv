"""
Junta a coleta legislativa com a execução orçamentária e grava site/dados.json.

O cruzamento é feito pela unidade orçamentária. A tabela do Congresso traz a UO
pelo nome (às vezes com erro de digitação), e o SIOP traz código e descrição —
então o casamento é por nome normalizado, com `config/de_para_uo.csv` para os
casos que o normalizador não resolve.

Dentro de cada UO, o painel separa dois recortes:

- **ações abertas por crédito**: dotação inicial zero e dotação atual positiva.
  É o recorte mais próximo do crédito extraordinário, embora também capture
  créditos especiais na mesma unidade.
- **total da unidade**: contexto, para dimensionar o peso do crédito.

Para atribuição exata, preencha `config/de_para_acoes.csv` com as ações listadas
no anexo da MP; quando houver linha para a matéria, ela substitui a heurística.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

DADOS = Path("dados")
CONFIG = Path("config")
SAIDA = Path("site/dados.json")

ABERTAS_POR_CREDITO = "aberta_por_credito"


def normalizar(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = txt.lower()
    txt = re.sub(r"^\s*\d{4,6}\s*[-–]\s*", "", txt)          # "32401 - ANSN"
    txt = re.sub(r"[^a-z0-9 ]", " ", txt)
    txt = re.sub(r"\b(recursos sob (a )?supervisao d[oae]s?)\b", "rsv", txt)
    txt = re.sub(r"\b(ministerio|secretaria|fundo|instituto|agencia|nacional|federal|"
                 r"da|de|do|das|dos|e|em|para|geral)\b", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def ler_csv(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def num(v) -> float:
    if v in (None, "", "NA"):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def carregar_siop(ano: int) -> list[dict]:
    linhas = ler_csv(DADOS / f"siop_{ano}.csv")
    saida = []
    for l in linhas:
        codigo = (l.get("codigo_uo") or "").strip()
        if not codigo:
            continue
        saida.append({
            "codigo_uo": codigo.zfill(5) if codigo.isdigit() else codigo,
            "unidade": (l.get("unidade") or "").strip(),
            "codigo_acao": (l.get("codigo_acao") or "").strip(),
            "acao": (l.get("acao") or "").strip(),
            "loa": num(l.get("loa")),
            "atual": num(l.get("loa_mais_credito")),
            "empenhado": num(l.get("empenhado")),
            "liquidado": num(l.get("liquidado")),
            "pago": num(l.get("pago")),
        })
    return saida


def indice_uo(siop: list[dict], de_para: list[dict]) -> dict[str, str]:
    """nome normalizado -> código da UO"""
    indice: dict[str, str] = {}
    for l in siop:
        chave = normalizar(l["unidade"])
        if chave:
            indice.setdefault(chave, l["codigo_uo"])
    for l in de_para:
        nome = normalizar(l.get("nome_congresso", ""))
        codigo = (l.get("codigo_uo") or "").strip()
        if nome and codigo:
            indice[nome] = codigo.zfill(5)
    return indice


def agregar(linhas: list[dict]) -> dict:
    return {
        "dotacao_inicial": round(sum(l["loa"] for l in linhas), 2),
        "dotacao_atual": round(sum(l["atual"] for l in linhas), 2),
        "empenhado": round(sum(l["empenhado"] for l in linhas), 2),
        "liquidado": round(sum(l["liquidado"] for l in linhas), 2),
        "pago": round(sum(l["pago"] for l in linhas), 2),
        "acoes": len({l["codigo_acao"] for l in linhas if l["codigo_acao"]}),
    }


def dias_ate(alvo: str | None, hoje: date) -> int | None:
    if not alvo:
        return None
    return (date.fromisoformat(alvo) - hoje).days


def main() -> None:
    hoje = date.today()
    propostas = json.loads((DADOS / "congresso.json").read_text(encoding="utf-8"))
    mpvs = [p for p in propostas if p.get("tipo", "").lower().startswith("crédito extraordin")]

    de_para_uo = ler_csv(CONFIG / "de_para_uo.csv")
    de_para_acoes = defaultdict(set)
    for l in ler_csv(CONFIG / "de_para_acoes.csv"):
        ident = (l.get("identificacao") or "").strip()
        acao = (l.get("codigo_acao") or "").strip()
        if ident and acao:
            de_para_acoes[ident].add(acao)

    anos = sorted({p["ano"] for p in mpvs})
    siop_por_ano = {ano: carregar_siop(ano) for ano in anos}
    indices = {ano: indice_uo(linhas, de_para_uo) for ano, linhas in siop_por_ano.items()}

    nao_casadas: set[str] = set()
    registros = []

    for p in mpvs:
        siop = siop_por_ano.get(p["ano"], [])
        indice = indices.get(p["ano"], {})
        por_uo = defaultdict(list)
        for l in siop:
            por_uo[l["codigo_uo"]].append(l)

        unidades = []
        for u in p.get("unidades", []):
            codigo = indice.get(normalizar(u["unidade"]))
            if not codigo:
                nao_casadas.add(u["unidade"])
            linhas = por_uo.get(codigo, []) if codigo else []
            fixadas = de_para_acoes.get(p["identificacao"])
            if fixadas:
                recorte = [l for l in linhas if l["codigo_acao"] in fixadas]
                criterio = "de-para manual"
            else:
                recorte = [l for l in linhas if l["loa"] == 0 and l["atual"] > 0]
                criterio = "ações abertas por crédito"
            unidades.append({
                "orgao": u["orgao"],
                "unidade": u["unidade"],
                "codigo_uo": codigo,
                "valor_credito": u["valor"],
                "criterio": criterio,
                "execucao": agregar(recorte),
                "unidade_total": agregar(linhas),
            })

        soma = lambda campo, chave: round(  # noqa: E731
            sum(u[chave][campo] for u in unidades), 2)
        execucao = {
            "dotacao_atual": soma("dotacao_atual", "execucao"),
            "empenhado": soma("empenhado", "execucao"),
            "liquidado": soma("liquidado", "execucao"),
            "pago": soma("pago", "execucao"),
        }
        base = execucao["dotacao_atual"] or p.get("valor_total") or 0
        execucao["pct_empenhado"] = round(100 * execucao["empenhado"] / base, 1) if base else None
        execucao["pct_pago"] = round(100 * execucao["pago"] / base, 1) if base else None
        execucao["cobertura"] = round(
            100 * sum(1 for u in unidades if u["codigo_uo"]) / len(unidades), 0
        ) if unidades else 0

        vigente = None
        if p.get("vigencia_fim"):
            vigente = p["vigencia_fim"] >= hoje.isoformat() and p.get("situacao") not in (
                "SEM EFICÁCIA", "TRANSFORMADA EM NORMA JURÍDICA", "REJEITADA", "ARQUIVADA")

        registros.append({
            **p,
            "vigente": vigente,
            "dias_para_vigencia": dias_ate(p.get("vigencia_fim"), hoje),
            "dias_para_emendas": dias_ate(p.get("emendas_fim"), hoje),
            "emendas_abertas": bool(
                p.get("emendas_fim") and p["emendas_fim"] >= hoje.isoformat()),
            "unidades": unidades,
            "execucao": execucao,
        })

    registros.sort(key=lambda r: (r["ano"], r["numero"]), reverse=True)

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps({
        "atualizado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "anos": anos,
        "fontes": {
            "legislativo": "Congresso Nacional — Propostas Orçamentárias (PLNs e MPVs)",
            "vigencia": "Dados Abertos do Senado + cálculo art. 62 da Constituição",
            "execucao": "SIOP/SOF via pacote orcamentoBR",
        },
        "uo_sem_correspondencia": sorted(nao_casadas),
        "medidas": registros,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(registros)} MPs de crédito extraordinário -> {SAIDA}", file=sys.stderr)
    if nao_casadas:
        print("UOs sem correspondência no SIOP (adicione em config/de_para_uo.csv):",
              file=sys.stderr)
        for nome in sorted(nao_casadas):
            print(f"  - {nome}", file=sys.stderr)


if __name__ == "__main__":
    main()
