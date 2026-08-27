"""
Consolida os PLNs de crédito suplementar e especial em docs/dados-plns.json.

Duas coisas distinguem este painel do de créditos extraordinários:

**PLN não caduca.** Projeto de lei não tem prazo constitucional de vigência: ele
tramita até ser aprovado, rejeitado ou arquivado. Não há relógio correndo contra
a matéria, então aqui não existe régua de vigência nem data de caducidade. O que
há é o andamento — apresentação, despacho, janela de emendas, relatoria — e a
situação.

**Especial e suplementar não são a mesma coisa.**

- *Crédito especial* cria ação orçamentária **nova**: a programação não existia
  na lei orçamentária. No SIOP a linha aparece com dotação inicial zero.
- *Crédito suplementar* reforça ação **já existente**. A linha tem dotação
  inicial positiva, e é ela a base sobre a qual o acréscimo é medido.

Para os suplementares o painel mostra, por ação, quanto havia na lei
orçamentária, quanto o PLN acrescenta e o aumento em percentual — que é a
informação que diz se o projeto ajusta a margem ou dobra a programação.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from consolidar import ENCERRADAS, carregar_siop, indice_uo, ler_csv, normalizar

DADOS = Path("dados")
CONFIG = Path("config")
SAIDA = Path("docs/dados-plns.json")

TIPOS = {"crédito suplementar": "suplementar", "crédito especial": "especial"}


def cancelamentos(anexo: dict, siop_acoes: dict, ano_uo: dict) -> list[dict]:
    """De onde sai o dinheiro: as dotações anuladas para financiar o crédito.

    Crédito suplementar por anulação não cria recurso novo — ele move dotação de
    uma programação para outra. Sem essa metade, a ficha diz o que ganha e
    esconde o que perde.
    """
    por_uo: dict[str, dict] = {}
    for l in anexo.get("cancelamento", []):
        uo = por_uo.setdefault(l["uo_codigo"], {
            "codigo_uo": l["uo_codigo"], "unidade": l["unidade"],
            "valor": 0.0, "acoes": []})
        uo["valor"] += l["valor"]
        chave = (l["uo_codigo"], l["acao"].upper())
        linha = siop_acoes.get(chave)
        uo["acoes"].append({
            "codigo": l["acao"].upper(),
            "subtitulo": (l.get("localizador") or "").strip() or None,
            "descricao": l.get("descricao", ""),
            "valor": l["valor"],
            "dotacao_inicial": linha["loa"] if linha else None,
            "reducao_pct": round(100 * l["valor"] / linha["loa"], 1)
            if linha and linha["loa"] > 0 else None,
        })
    for uo in por_uo.values():
        uo["valor"] = round(uo["valor"], 2)
        uo["acoes"].sort(key=lambda a: -a["valor"])
    return sorted(por_uo.values(), key=lambda u: -u["valor"])


def unidades_do_anexo(anexo: dict) -> dict:
    por_uo: dict[str, dict] = {}
    for l in anexo.get("programatica", []):
        uo = por_uo.setdefault(l["uo_codigo"], {
            "codigo_uo": l["uo_codigo"], "unidade": l["unidade"],
            "orgao": l.get("orgao", ""), "valor": 0.0, "acoes": {}})
        uo["valor"] += l["valor"]
        chave = (l["acao"].upper(), (l.get("localizador") or "").strip())
        atual = uo["acoes"].setdefault(chave, {"valor": 0.0, "descricao": l.get("descricao", "")})
        atual["valor"] += l["valor"]
    return por_uo


def medir(linha: dict | None, valor_credito: float, especie: str) -> dict:
    """Compara o crédito com o que já havia na lei orçamentária.

    Para suplementar, a dotação inicial é a base do acréscimo. Para especial ela
    é zero por definição — a ação não existia — e o percentual não faz sentido.
    """
    if linha is None:
        return {"valor": round(valor_credito, 2), "encontrada": False,
                "dotacao_inicial": None, "dotacao_atual": None,
                "aumento_pct": None, "empenhado": None, "pago": None,
                "nova": especie == "especial"}

    loa = linha["loa"]
    nova = loa == 0
    aumento = round(100 * valor_credito / loa, 1) if loa > 0 else None
    return {
        "valor": round(valor_credito, 2),
        "encontrada": True,
        "nova": nova,
        "dotacao_inicial": loa,
        "dotacao_atual": linha["atual"],
        "aumento_pct": aumento,
        "empenhado": linha["empenhado"],
        "pago": linha["pago"],
        "descricao_siop": linha["acao"],
        "subtitulo_desc": linha.get("subtitulo") or None,
    }


def main() -> None:
    hoje = date.today().isoformat()
    propostas = json.loads((DADOS / "congresso.json").read_text(encoding="utf-8"))
    plns = [p for p in propostas
            if p.get("especie") == "PLN" and p.get("tipo", "").lower() in TIPOS]

    caminho = CONFIG / "anexos_plns.json"
    anexos = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    de_para_uo = ler_csv(CONFIG / "de_para_uo.csv")

    anos = sorted({p["ano"] for p in plns})
    siop = {ano: carregar_siop(ano) for ano in anos}
    indices = {ano: indice_uo(l, de_para_uo) for ano, l in siop.items()}
    por_subtitulo, por_acao, por_uo = {}, {}, {}
    for ano, linhas in siop.items():
        por_subtitulo[ano] = {(l["codigo_uo"], l["codigo_acao"], l["codigo_subtitulo"]): l
                              for l in linhas if l["codigo_subtitulo"]}
        agrupada: dict[tuple, dict] = {}
        for l in linhas:
            chave = (l["codigo_uo"], l["codigo_acao"])
            if chave not in agrupada:
                agrupada[chave] = dict(l)
            else:
                for c in ("loa", "atual", "credito", "empenhado", "liquidado", "pago"):
                    agrupada[chave][c] = round(agrupada[chave][c] + l[c], 2)
        por_acao[ano] = agrupada
        d = defaultdict(list)
        for l in linhas:
            d[l["codigo_uo"]].append(l)
        por_uo[ano] = d

    registros, sem_anexo = [], []

    # As unidades do anexo precisam estar na consulta ao SIOP, senão não há
    # dotação da LOA para comparar e todo suplementar aparece sem base. Isso já
    # aconteceu por ordem de etapas: o arquivo com as unidades dos PLNs era
    # escrito depois de a consulta rodar, e nunca chegava a tempo.
    pedidas = {l["uo_codigo"] for v in anexos.values() for l in v.get("programatica", [])}
    pedidas |= {l["uo_codigo"] for v in anexos.values() for l in v.get("cancelamento", [])}
    disponiveis = {l["codigo_uo"] for linhas in siop.values() for l in linhas}
    faltando = sorted(pedidas - disponiveis)
    if pedidas:
        print(f"  unidades do anexo no SIOP: {len(pedidas) - len(faltando)}/{len(pedidas)}",
              file=sys.stderr)
    if pedidas and not (pedidas & disponiveis):
        print("::error::nenhuma unidade dos PLNs está nos dados do SIOP — a consulta não as "
              "incluiu. Confira se coleta/anexos_plns.py roda ANTES de coleta/siop.R, para "
              "que dados/uos_plns.txt exista quando a consulta acontecer", file=sys.stderr)
    elif faltando:
        print(f"::warning::sem dados do SIOP para as unidades: {', '.join(faltando)}",
              file=sys.stderr)

    for p in plns:
        ident = p["identificacao"]
        especie = TIPOS[p["tipo"].lower()]
        anexo = anexos.get(ident) or {}
        prog = unidades_do_anexo(anexo)
        subs = por_subtitulo.get(p["ano"], {})
        acoes_siop = por_acao.get(p["ano"], {})
        indice = indices.get(p["ano"], {})

        unidades = []
        if prog:
            origem = "anexo"
            for uo in prog.values():
                acoes = []
                for (acao, localizador), dados in sorted(
                        uo["acoes"].items(), key=lambda kv: -kv[1]["valor"]):
                    linha = subs.get((uo["codigo_uo"], acao, localizador)) \
                        or acoes_siop.get((uo["codigo_uo"], acao))
                    acoes.append({
                        "codigo": acao, "subtitulo": localizador or None,
                        "descricao": dados["descricao"],
                        **medir(linha, dados["valor"], especie),
                    })
                unidades.append({
                    "orgao": uo["orgao"], "unidade": uo["unidade"],
                    "codigo_uo": uo["codigo_uo"],
                    "valor_credito": round(uo["valor"], 2),
                    "acoes": acoes,
                })
        else:
            origem = "tabela do Congresso"
            sem_anexo.append(ident)
            grupos = por_uo.get(p["ano"], {})
            for u in p.get("unidades", []):
                codigo = indice.get(normalizar(u["unidade"]))
                linhas = grupos.get(codigo, []) if codigo else []
                loa_uo = round(sum(l["loa"] for l in linhas), 2)
                unidades.append({
                    "orgao": u["orgao"], "unidade": u["unidade"], "codigo_uo": codigo,
                    "valor_credito": u["valor"], "acoes": [],
                    "loa_unidade": loa_uo or None,
                    "aumento_uo_pct": round(100 * u["valor"] / loa_uo, 1) if loa_uo else None,
                })

        com_base = [a for u in unidades for a in u["acoes"] if a.get("aumento_pct") is not None]
        maior = max(com_base, key=lambda a: a["aumento_pct"], default=None)
        base_total = round(sum(a["dotacao_inicial"] or 0
                               for u in unidades for a in u["acoes"]), 2)
        credito_total = round(sum(u["valor_credito"] for u in unidades), 2)

        anulacoes = cancelamentos(anexo, acoes_siop, por_uo.get(p["ano"], {}))
        total_anulado = round(sum(u["valor"] for u in anulacoes), 2)

        registros.append({
            **p,
            "especie_credito": especie,
            "cancelamentos": anulacoes,
            "total_cancelamento": total_anulado or None,
            "autor": anexo.get("autor"),
            "local": anexo.get("local"),
            "acao_legislativa": anexo.get("acao_legislativa"),
            "origem": origem,
            "encerrado": p.get("situacao") in ENCERRADAS,
            "sem_relator": not p.get("relator"),
            "apresentacao": anexo.get("apresentacao"),
            "despacho": anexo.get("despacho"),
            "mensagem": anexo.get("mensagem"),
            "norma": p.get("norma") or anexo.get("norma"),
            "situacao_prazo": anexo.get("situacao_prazo"),
            "emendas_abertas": bool(p.get("emendas_fim") and p["emendas_fim"] >= hoje),
            "unidades": unidades,
            "acoes_novas": sum(1 for u in unidades for a in u["acoes"] if a.get("nova")),
            "acoes_total": sum(len(u["acoes"]) for u in unidades),
            "base_loa": base_total or None,
            "aumento_medio_pct": round(100 * credito_total / base_total, 1)
            if base_total else None,
            "maior_aumento": {"acao": maior["codigo"], "pct": maior["aumento_pct"],
                              "descricao": maior["descricao"]} if maior else None,
        })

    registros.sort(key=lambda r: (r["ano"], r["numero"]), reverse=True)

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps({
        "atualizado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "anos": anos,
        "sem_anexo": sorted(sem_anexo),
        "plns": registros,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    por_especie = defaultdict(int)
    for r in registros:
        por_especie[r["especie_credito"]] += 1
    print(f"{len(registros)} PLNs -> {SAIDA} ({dict(por_especie)})", file=sys.stderr)
    if sem_anexo:
        print(f"  sem anexo (só unidade orçamentária): {', '.join(sem_anexo)}", file=sys.stderr)


if __name__ == "__main__":
    main()
