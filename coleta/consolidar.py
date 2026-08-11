"""
Junta a coleta legislativa com a execução orçamentária e grava docs/dados.json.

O cruzamento tem duas vias, e a primeira é a boa:

**Pela programática do anexo** (`config/anexos.json`). O anexo da MP traz o
código da unidade orçamentária e o código da ação. Com esse par, a linha do SIOP
é localizada exatamente — sem casar nome de unidade, sem adivinhar quais ações da
unidade pertencem ao crédito.

**Pelo nome da unidade**, quando o anexo não foi obtido. É o comportamento
antigo, aproximado, e a ficha avisa.

Localizada a ação, resta separar o que da execução dela pertence a esta MP:

- **direta** — a ação nasceu do crédito (dotação inicial zero na LOA). A
  execução da ação é a execução do crédito.
- **piso** — a ação já existia na LOA e foi reforçada. Não há como separar o que
  foi gasto por conta da LOA, então conta-se `empenhado − dotação inicial`
  quando positivo: só esse excedente veio necessariamente do crédito. Aparece
  com `≥`.

Sobre isso aplica-se o rateio quando a mesma ação recebeu crédito de mais de uma
MP no exercício: cada uma responde pela fração que abriu.
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
SAIDA = Path("docs/dados.json")

ENCERRADAS = {"SEM EFICÁCIA", "TRANSFORMADA EM NORMA JURÍDICA",
              "REJEITADA", "ARQUIVADA", "PREJUDICADA"}


def normalizar(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = txt.lower()
    txt = re.sub(r"^\s*\d{4,6}\s*[-–]\s*", "", txt)
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
        loa, atual = num(l.get("loa")), num(l.get("loa_mais_credito"))
        emp, liq, pg = num(l.get("empenhado")), num(l.get("liquidado")), num(l.get("pago"))
        saida.append({
            "codigo_uo": codigo.zfill(5) if codigo.isdigit() else codigo,
            "unidade": (l.get("unidade") or "").strip(),
            "codigo_acao": (l.get("codigo_acao") or "").strip().upper(),
            "acao": (l.get("acao") or "").strip(),
            "codigo_subtitulo": (l.get("codigo_subtitulo") or "").strip(),
            "subtitulo": (l.get("subtitulo") or "").strip(),
            "loa": loa, "atual": atual, "credito": round(atual - loa, 2),
            "empenhado": emp, "liquidado": liq, "pago": pg,
            "empenhado_alem_loa": round(max(0.0, emp - loa), 2),
            "liquidado_alem_loa": round(max(0.0, liq - loa), 2),
            "pago_alem_loa": round(max(0.0, pg - loa), 2),
        })
    return saida


def indice_uo(siop: list[dict], de_para: list[dict]) -> dict[str, str]:
    indice: dict[str, str] = {}
    for l in siop:
        chave = normalizar(l["unidade"])
        if chave:
            indice.setdefault(chave, l["codigo_uo"])
    for l in de_para:
        nome, codigo = normalizar(l.get("nome_congresso", "")), (l.get("codigo_uo") or "").strip()
        if nome and codigo:
            indice[nome] = codigo.zfill(5) if codigo.isdigit() else codigo
    return indice


def atribuir(pares: list[tuple[dict, float]]) -> dict:
    """Atribui a execução à MP.

    `pares` são (linha do SIOP, valor que esta MP abriu naquela linha).

    O modo de cada linha diz o quanto se pode afirmar sobre ela:

    - **exata** — a linha foi localizada por UO + ação + subtítulo. O crédito
      extraordinário abre subtítulo próprio, e é ele que o anexo informa, então
      essa linha é o crédito: toda a execução dela pertence à MP. Não se
      desconta dotação inicial, porque crédito extraordinário não reforça
      orçamento vigente — ele é extraorçamentário e vive em linha separada.
    - **piso (≥)** — não houve subtítulo para casar e a linha da ação mistura
      LOA e crédito. Conta-se só o empenho que excede a dotação inicial.
    - **direta** — sem subtítulo, mas a ação inteira nasceu do crédito
      (dotação inicial zero), o que torna a mistura impossível.

    Em qualquer modo, se mais de uma MP alimenta a mesma linha, cada uma
    responde pela fração que abriu.
    """
    total = {"base": 0.0, "empenhado": 0.0, "liquidado": 0.0, "pago": 0.0,
             "credito_no_ano": 0.0, "dotacao_inicial": 0.0, "dotacao_atual": 0.0}
    modos, rateado, detalhe, contaminadas = set(), False, [], 0

    for linha, valor_mp in pares:
        credito = linha["credito"]
        exata = bool(linha.get("codigo_subtitulo"))

        if exata:
            modo = "exata"
            # o subtítulo do crédito deve ter dotação inicial zero. Se não tem,
            # ele acumula LOA ou outro crédito, e o número deixa de ser limpo:
            # é o caso de suplementar e extraordinário na mesma linha.
            if linha["loa"] > 0:
                contaminadas += 1
                modo = "piso"
        elif linha["loa"] == 0:
            modo = "direta"
        else:
            modo = "piso"
        modos.add(modo)

        fator = min(1.0, valor_mp / credito) if credito > 0 else 0.0
        if fator < 0.999:
            rateado = True

        for cheio, piso in (("empenhado", "empenhado_alem_loa"),
                            ("liquidado", "liquidado_alem_loa"),
                            ("pago", "pago_alem_loa")):
            total[cheio] += linha[piso if modo == "piso" else cheio] * fator
        total["base"] += valor_mp
        total["credito_no_ano"] += credito
        total["dotacao_inicial"] += linha["loa"]
        total["dotacao_atual"] += linha["atual"]

        detalhe.append({
            "acao": linha["codigo_acao"], "descricao": linha["acao"], "modo": modo,
            "subtitulo": linha.get("codigo_subtitulo") or None,
            "subtitulo_desc": linha.get("subtitulo") or None,
            "valor_mp": round(valor_mp, 2), "credito_no_ano": credito,
            "fator": round(fator, 4), "dotacao_inicial": linha["loa"],
            "empenhado": round(linha["empenhado_alem_loa" if modo == "piso" else "empenhado"] * fator, 2),
            "pago": round(linha["pago_alem_loa" if modo == "piso" else "pago"] * fator, 2),
        })

    if "piso" in modos:
        modo_geral = "piso"
    elif "exata" in modos:
        modo_geral = "exata"
    else:
        modo_geral = "direta"

    return {
        "modo": modo_geral,
        "rateado": rateado,
        "contaminadas": contaminadas,
        **{k: round(v, 2) for k, v in total.items()},
        "acoes": len(detalhe),
        "detalhe_acoes": sorted(detalhe, key=lambda d: -d["valor_mp"])[:12],
    }


def unidades_do_anexo(anexo: dict) -> dict:
    """Agrupa a programática do anexo por unidade orçamentária.

    A chave dentro da unidade é o par ação + subtítulo, porque é o subtítulo
    que identifica a linha do crédito extraordinário dentro da ação.
    """
    por_uo: dict[str, dict] = {}
    for l in anexo.get("programatica", []):
        uo = por_uo.setdefault(l["uo_codigo"], {
            "codigo_uo": l["uo_codigo"], "unidade": l["unidade"],
            "orgao": l.get("orgao", ""), "valor": 0.0, "acoes": defaultdict(float),
            "qualificadores": set()})
        uo["valor"] += l["valor"]
        chave = (l["acao"].upper(), (l.get("localizador") or "").strip())
        uo["acoes"][chave] += l["valor"]
        if l.get("gnd"):
            uo["qualificadores"].add(f"{l['gnd']} · RP {l.get('rp') or '—'} · fonte {l.get('fonte') or '—'}")
    return por_uo


def dias_ate(alvo: str | None, hoje: date) -> int | None:
    return (date.fromisoformat(alvo) - hoje).days if alvo else None


def main() -> None:
    hoje = date.today()
    propostas = json.loads((DADOS / "congresso.json").read_text(encoding="utf-8"))
    mpvs = [p for p in propostas if p.get("tipo", "").lower().startswith("crédito extraordin")]

    caminho_anexos = CONFIG / "anexos.json"
    anexos = json.loads(caminho_anexos.read_text(encoding="utf-8")) if caminho_anexos.exists() else {}
    de_para_uo = ler_csv(CONFIG / "de_para_uo.csv")
    de_para_acoes = defaultdict(set)
    for l in ler_csv(CONFIG / "de_para_acoes.csv"):
        ident, acao = (l.get("identificacao") or "").strip(), (l.get("codigo_acao") or "").strip().upper()
        if ident and acao:
            de_para_acoes[ident].add(acao)

    anos = sorted({p["ano"] for p in mpvs})
    siop_por_ano = {ano: carregar_siop(ano) for ano in anos}
    indices = {ano: indice_uo(l, de_para_uo) for ano, l in siop_por_ano.items()}

    # Dois índices: o do subtítulo é o bom, o da ação é a queda quando o
    # subtítulo não veio na consulta ou não bate com o do anexo. As linhas da
    # ação são somadas para que a queda continue produzindo um número.
    por_subtitulo, por_acao = {}, {}
    for ano, linhas in siop_por_ano.items():
        por_subtitulo[ano] = {
            (l["codigo_uo"], l["codigo_acao"], l["codigo_subtitulo"]): l
            for l in linhas if l["codigo_subtitulo"]
        }
        agrupadas: dict[tuple, dict] = {}
        for l in linhas:
            chave = (l["codigo_uo"], l["codigo_acao"])
            if chave not in agrupadas:
                agrupadas[chave] = {**l, "codigo_subtitulo": "", "subtitulo": ""}
            else:
                for campo in ("loa", "atual", "credito", "empenhado", "liquidado", "pago",
                              "empenhado_alem_loa", "liquidado_alem_loa", "pago_alem_loa"):
                    agrupadas[chave][campo] = round(agrupadas[chave][campo] + l[campo], 2)
        por_acao[ano] = agrupadas

    por_uo_ano = {}
    for ano, linhas in siop_por_ano.items():
        d = defaultdict(list)
        for l in linhas:
            d[l["codigo_uo"]].append(l)
        por_uo_ano[ano] = d

    nao_casadas, sem_anexo, acoes_ausentes = set(), [], []
    registros = []

    for p in mpvs:
        ident = p["identificacao"]
        subs = por_subtitulo.get(p["ano"], {})
        chaves = por_acao.get(p["ano"], {})
        anexo = anexos.get(ident) or {}
        prog = unidades_do_anexo(anexo)
        unidades = []

        if prog:
            origem = "anexo"
            for uo in prog.values():
                pares, faltando = [], []
                for (acao, localizador), valor in uo["acoes"].items():
                    linha = subs.get((uo["codigo_uo"], acao, localizador))
                    if linha is None:
                        linha = chaves.get((uo["codigo_uo"], acao))
                    if linha:
                        pares.append((linha, valor))
                    else:
                        faltando.append(f"{acao}/{localizador}")
                        acoes_ausentes.append(
                            f"{ident} · UO {uo['codigo_uo']} · ação {acao} · subtítulo {localizador}")
                unidades.append({
                    "orgao": uo["orgao"], "unidade": uo["unidade"],
                    "codigo_uo": uo["codigo_uo"], "valor_credito": round(uo["valor"], 2),
                    "acoes_credito": sorted(f"{a}/{s}" for a, s in uo["acoes"]),
                    "acoes_ausentes": faltando,
                    "qualificadores": sorted(uo["qualificadores"])[:4],
                    "execucao": atribuir(pares),
                })
        else:
            origem = "nome da unidade"
            sem_anexo.append(ident)
            indice = indices.get(p["ano"], {})
            grupos = por_uo_ano.get(p["ano"], {})
            fixadas = de_para_acoes.get(ident)
            for u in p.get("unidades", []):
                codigo = indice.get(normalizar(u["unidade"]))
                if not codigo:
                    nao_casadas.add(u["unidade"])
                linhas = grupos.get(codigo, []) if codigo else []
                if fixadas:
                    recorte = [l for l in linhas if l["codigo_acao"] in fixadas]
                else:
                    recorte = [l for l in linhas if l["credito"] > 0]
                total_credito = sum(l["credito"] for l in recorte) or 1.0
                pares = [(l, u["valor"] * l["credito"] / total_credito) for l in recorte]
                unidades.append({
                    "orgao": u["orgao"], "unidade": u["unidade"], "codigo_uo": codigo,
                    "valor_credito": u["valor"], "acoes_credito": [], "acoes_ausentes": [],
                    "qualificadores": [], "execucao": atribuir(pares),
                })

        def soma(campo: str) -> float:
            return round(sum(u["execucao"][campo] for u in unidades), 2)

        modos = {u["execucao"]["modo"] for u in unidades} or {"direta"}
        if "piso" in modos:
            modo_mp = "piso"
        elif "exata" in modos:
            modo_mp = "exata"
        else:
            modo_mp = "direta"
        contaminadas = sum(u["execucao"].get("contaminadas", 0) for u in unidades)
        base = soma("base")
        execucao = {
            "origem": origem,
            "modo": modo_mp,
            "contaminadas": contaminadas,
            "rateado": any(u["execucao"]["rateado"] for u in unidades),
            "base": base,
            "credito_no_ano": soma("credito_no_ano"),
            "empenhado": soma("empenhado"),
            "liquidado": soma("liquidado"),
            "pago": soma("pago"),
            "acoes": sum(u["execucao"]["acoes"] for u in unidades),
            "acoes_ausentes": sum(len(u["acoes_ausentes"]) for u in unidades),
        }
        execucao["pct_empenhado"] = round(100 * execucao["empenhado"] / base, 1) if base else None
        execucao["pct_pago"] = round(100 * execucao["pago"] / base, 1) if base else None

        encerrada = p.get("situacao") in ENCERRADAS
        vigente = None
        if p.get("vigencia_fim"):
            vigente = p["vigencia_fim"] >= hoje.isoformat() and not encerrada

        registros.append({
            **p,
            "vigente": vigente, "encerrada": encerrada,
            "sem_relator": not p.get("relator"),
            "urgencia": p.get("urgencia") or anexo.get("urgencia"),
            "prorrogada": bool(p.get("prorrogada")),
            "ato_prorrogacao": p.get("ato_prorrogacao"),
            "fim_periodo_atual": p.get("fim_periodo_atual"),
            "inicio_periodo_atual": p.get("inicio_periodo_atual"),
            "situacao_prazo": p.get("situacao_prazo"),
            "despacho": p.get("despacho"),
            "numero_camara": p.get("numero_camara"),
            "mensagem": p.get("mensagem"),
            "publicacao_dou": p.get("publicacao_dou"),
            "dias_para_prorrogacao": dias_ate(p.get("vigencia_60"), hoje)
            if not p.get("prorrogada") else None,
            "dias_para_urgencia": dias_ate(p.get("urgencia"), hoje),
            "dias_para_vigencia": dias_ate(p.get("vigencia_fim"), hoje),
            "dias_para_emendas": dias_ate(p.get("emendas_fim"), hoje),
            "emendas_abertas": bool(p.get("emendas_fim") and p["emendas_fim"] >= hoje.isoformat()),
            "unidades": unidades, "execucao": execucao,
        })

    registros.sort(key=lambda r: (r["ano"], r["numero"]), reverse=True)

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps({
        "atualizado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "anos": anos,
        "fontes": {
            "legislativo": "Congresso Nacional — Propostas Orçamentárias e páginas das MPs",
            "programatica": "Anexo da MP enviado pela Presidência da República",
            "execucao": "SIOP/SOF via pacote orcamentoBR",
        },
        "uo_sem_correspondencia": sorted(nao_casadas),
        "sem_anexo": sorted(sem_anexo),
        "acoes_ausentes_no_siop": sorted(set(acoes_ausentes)),
        "medidas": registros,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    por_origem, por_modo = defaultdict(int), defaultdict(int)
    for r in registros:
        por_origem[r["execucao"]["origem"]] += 1
        por_modo[r["execucao"]["modo"]] += 1
    contaminadas = sum(r["execucao"].get("contaminadas", 0) for r in registros)
    sem_execucao = sum(1 for r in registros if not r["execucao"]["base"])
    if registros and sem_execucao == len(registros):
        print("::error::nenhuma MP com dados de execução — o passo do SIOP não "
              "produziu dados/siop_*.csv ou o mapeamento de colunas falhou", file=sys.stderr)
    elif sem_execucao:
        print(f"::warning::{sem_execucao} de {len(registros)} MPs sem dados de execução",
              file=sys.stderr)
    print(f"{len(registros)} MPs -> {SAIDA}", file=sys.stderr)
    print(f"  origem do cruzamento: {dict(por_origem)} | atribuição: {dict(por_modo)}",
          file=sys.stderr)
    if contaminadas:
        print(f"::warning::{contaminadas} linha(s) de subtítulo com dotação inicial acima de "
              "zero — a linha acumula LOA ou outro crédito e caiu no modo piso", file=sys.stderr)
    if sem_anexo:
        print(f"  sem anexo (cruzamento aproximado): {', '.join(sem_anexo)}", file=sys.stderr)
    if acoes_ausentes:
        print("  ações do anexo sem linha no SIOP (crédito ainda não registrado?):", file=sys.stderr)
        for a in sorted(set(acoes_ausentes)):
            print(f"    - {a}", file=sys.stderr)
    if nao_casadas:
        print("  UOs sem correspondência (config/de_para_uo.csv):", file=sys.stderr)
        for n in sorted(nao_casadas):
            print(f"    - {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
