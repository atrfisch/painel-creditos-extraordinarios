"""
Junta a coleta legislativa com a execução orçamentária e grava site/dados.json.

O cruzamento é por unidade orçamentária: a tabela do Congresso traz o nome (às
vezes com erro de digitação), o SIOP traz código e descrição, e o casamento é por
nome normalizado, com `config/de_para_uo.csv` para o que sobrar.

Dentro da unidade, o recorte são as ações que receberam crédito no exercício
(dotação atual maior que a inicial). A atribuição dessa execução à MP acontece em
um de três modos:

- **direta**  — a ação nasceu do crédito (dotação inicial zero). A execução da
  ação é a execução do crédito.
- **piso**    — a ação já existia na LOA e foi reforçada. Não há como separar o
  que foi executado por conta da LOA. O painel usa `empenhado − dotação inicial`
  quando positivo: se a ação gastou além do que a LOA autorizava, o excedente
  veio do crédito. É piso demonstrável, não estimativa.
- **manual**  — há linha para a matéria em `config/de_para_acoes.csv`, com as
  ações do anexo da MP. Substitui a heurística.

Quando mais de uma MP do mesmo ano incide sobre a mesma unidade, os números da
ação são idênticos para todas elas. O painel marca esses casos como ambíguos em
vez de repetir o valor como se fosse de cada uma.
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

ENCERRADAS = {"SEM EFICÁCIA", "TRANSFORMADA EM NORMA JURÍDICA",
              "REJEITADA", "ARQUIVADA", "PREJUDICADA"}


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
    saida = []
    for l in ler_csv(DADOS / f"siop_{ano}.csv"):
        codigo = (l.get("codigo_uo") or "").strip()
        if not codigo:
            continue
        loa = num(l.get("loa"))
        atual = num(l.get("loa_mais_credito"))
        empenhado = num(l.get("empenhado"))
        pago = num(l.get("pago"))
        saida.append({
            "codigo_uo": codigo.zfill(5) if codigo.isdigit() else codigo,
            "unidade": (l.get("unidade") or "").strip(),
            "codigo_acao": (l.get("codigo_acao") or "").strip(),
            "acao": (l.get("acao") or "").strip(),
            "loa": loa,
            "atual": atual,
            "credito": round(atual - loa, 2),
            "empenhado": empenhado,
            "liquidado": num(l.get("liquidado")),
            "pago": pago,
            # o que necessariamente veio do crédito: gasto acima do que a LOA autorizava
            "empenhado_alem_loa": round(max(0.0, empenhado - loa), 2),
            "liquidado_alem_loa": round(max(0.0, num(l.get("liquidado")) - loa), 2),
            "pago_alem_loa": round(max(0.0, pago - loa), 2),
        })
    return saida


def indice_uo(siop: list[dict], de_para: list[dict]) -> dict[str, str]:
    indice: dict[str, str] = {}
    for l in siop:
        chave = normalizar(l["unidade"])
        if chave:
            indice.setdefault(chave, l["codigo_uo"])
    for l in de_para:
        nome = normalizar(l.get("nome_congresso", ""))
        codigo = (l.get("codigo_uo") or "").strip()
        if nome and codigo:
            indice[nome] = codigo.zfill(5) if codigo.isdigit() else codigo
    return indice


def agregar(linhas: list[dict], modo: str, valor_mp: float) -> dict:
    """Consolida as ações de uma unidade e atribui a parcela desta MP.

    Uma ação pode ter recebido crédito de mais de uma MP no mesmo exercício — a
    ANP teve quatro em 2026. O SIOP registra um número só por ação, então repetir
    esse número em cada ficha faria a mesma execução aparecer várias vezes.

    O rateio resolve pela participação: se a MP abriu R$ 3,4 bi de um crédito
    total de R$ 4,0 bi naquela ação, ela responde por 86% do que foi executado.
    O denominador passa a ser o crédito da própria MP, de modo que o percentual
    exibido é a taxa de execução do crédito — compartilhada entre as MPs, como
    de fato é — enquanto os valores em reais ficam proporcionais a cada uma.
    """
    def soma(campo: str) -> float:
        return round(sum(l[campo] for l in linhas), 2)

    credito_siop = soma("credito")
    if modo == "piso":
        emp, liq, pg = (soma("empenhado_alem_loa"), soma("liquidado_alem_loa"),
                        soma("pago_alem_loa"))
    else:
        emp, liq, pg = soma("empenhado"), soma("liquidado"), soma("pago")

    fator = min(1.0, valor_mp / credito_siop) if credito_siop > 0 else 0.0
    parcela = lambda v: round(v * fator, 2)  # noqa: E731

    return {
        "modo": modo,
        "fator": round(fator, 4),
        "rateado": bool(credito_siop > 0 and fator < 0.999),
        "dotacao_inicial": soma("loa"),
        "dotacao_atual": soma("atual"),
        "credito_no_ano": credito_siop,
        "base": round(valor_mp, 2) if credito_siop > 0 else 0.0,
        "empenhado": parcela(emp),
        "liquidado": parcela(liq),
        "pago": parcela(pg),
        "acoes": len({l["codigo_acao"] for l in linhas if l["codigo_acao"]}),
        "detalhe_acoes": [
            {"codigo": l["codigo_acao"], "acao": l["acao"], "dotacao_inicial": l["loa"],
             "credito": l["credito"], "empenhado": l["empenhado"], "pago": l["pago"]}
            for l in sorted(linhas, key=lambda x: -x["credito"])[:12]
        ],
    }


def dias_ate(alvo: str | None, hoje: date) -> int | None:
    return (date.fromisoformat(alvo) - hoje).days if alvo else None


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
    indices = {ano: indice_uo(l, de_para_uo) for ano, l in siop_por_ano.items()}

    # quais MPs disputam a mesma unidade no mesmo ano
    codigo_por_mp: dict[str, set[str]] = {}
    for p in mpvs:
        idx = indices.get(p["ano"], {})
        codigo_por_mp[p["identificacao"]] = {
            c for c in (idx.get(normalizar(u["unidade"])) for u in p.get("unidades", [])) if c
        }
    concorrentes: dict[tuple, list[str]] = defaultdict(list)
    for p in mpvs:
        for c in codigo_por_mp[p["identificacao"]]:
            concorrentes[(p["ano"], c)].append(p["identificacao"])

    nao_casadas: set[str] = set()
    registros = []

    for p in mpvs:
        siop = siop_por_ano.get(p["ano"], [])
        indice = indices.get(p["ano"], {})
        por_uo = defaultdict(list)
        for l in siop:
            por_uo[l["codigo_uo"]].append(l)

        fixadas = de_para_acoes.get(p["identificacao"])
        unidades: list[dict] = []
        ambigua = False

        for u in p.get("unidades", []):
            codigo = indice.get(normalizar(u["unidade"]))
            if not codigo:
                nao_casadas.add(u["unidade"])
            linhas = por_uo.get(codigo, []) if codigo else []

            if fixadas:
                recorte = [l for l in linhas if l["codigo_acao"] in fixadas]
                modo = "manual"
            else:
                recorte = [l for l in linhas if l["credito"] > 0]
                if recorte and all(l["loa"] == 0 for l in recorte):
                    modo = "direta"
                elif recorte:
                    modo = "piso"
                else:
                    modo = "direta"

            outras = [i for i in concorrentes.get((p["ano"], codigo), [])
                      if i != p["identificacao"]] if codigo else []
            if outras and modo != "manual":
                ambigua = True

            unidades.append({
                "orgao": u["orgao"],
                "unidade": u["unidade"],
                "codigo_uo": codigo,
                "valor_credito": u["valor"],
                "outras_mps": outras,
                "execucao": agregar(recorte, modo, u["valor"]),
            })

        def soma(campo: str) -> float:
            return round(sum(u["execucao"][campo] for u in unidades), 2)

        modos = {u["execucao"]["modo"] for u in unidades} or {"direta"}
        modo_mp = "manual" if "manual" in modos else ("piso" if "piso" in modos else "direta")
        base = soma("base")
        rateado = any(u["execucao"]["rateado"] for u in unidades)
        execucao = {
            "modo": modo_mp,
            "rateado": rateado,
            "ambigua": ambigua,
            "base": base,
            "dotacao_atual": soma("dotacao_atual"),
            "credito_no_ano": soma("credito_no_ano"),
            "empenhado": soma("empenhado"),
            "liquidado": soma("liquidado"),
            "pago": soma("pago"),
            "cobertura": round(100 * sum(1 for u in unidades if u["codigo_uo"]) / len(unidades))
            if unidades else 0,
        }
        execucao["pct_empenhado"] = round(100 * execucao["empenhado"] / base, 1) if base else None
        execucao["pct_pago"] = round(100 * execucao["pago"] / base, 1) if base else None

        encerrada = p.get("situacao") in ENCERRADAS
        vigente = None
        if p.get("vigencia_fim"):
            vigente = p["vigencia_fim"] >= hoje.isoformat() and not encerrada

        registros.append({
            **p,
            "vigente": vigente,
            "encerrada": encerrada,
            "sem_relator": not p.get("relator"),
            "dias_para_vigencia": dias_ate(p.get("vigencia_fim"), hoje),
            "dias_para_emendas": dias_ate(p.get("emendas_fim"), hoje),
            "emendas_abertas": bool(p.get("emendas_fim") and p["emendas_fim"] >= hoje.isoformat()),
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

    contagem: dict[str, int] = defaultdict(int)
    for r in registros:
        contagem[r["execucao"]["modo"]] += 1
    print(f"{len(registros)} MPs -> {SAIDA}", file=sys.stderr)
    print(f"  atribuição: {dict(contagem)} | rateadas: "
          f"{sum(1 for r in registros if r['execucao']['rateado'])}", file=sys.stderr)
    if nao_casadas:
        print("UOs sem correspondência (adicione em config/de_para_uo.csv):", file=sys.stderr)
        for nome in sorted(nao_casadas):
            print(f"  - {nome}", file=sys.stderr)


if __name__ == "__main__":
    main()
