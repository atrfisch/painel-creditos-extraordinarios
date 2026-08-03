"""
Coleta a tabela de Propostas Orçamentárias (PLNs e MPVs) do Congresso Nacional.

Fonte: https://www.congressonacional.leg.br/web/orcamento/acompanhe/propostas

A página é renderizada no servidor (não precisa de navegador). O parser abaixo é
deliberadamente tolerante: em vez de depender de classes CSS do Liferay, ele
ancora cada registro no link da matéria (.../materia/<codigo>) e lê o restante
por expressões regulares sobre o texto da linha. Se o layout mudar, o que quebra
é um campo, não a coleta inteira.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.congressonacional.leg.br/web/orcamento/acompanhe/propostas"
HEADERS = {
    "User-Agent": "painel-creditos-extraordinarios/1.0 (+github pages; contato via repositorio)",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

RE_IDENT = re.compile(r"^\s*(MPV|PLN)\s*(\d+)\s*/\s*(\d{4})\s*$", re.I)
RE_MATERIA = re.compile(r"/materia/(\d+)")
RE_TIPO = re.compile(r"\((Crédito Extraordinário|Crédito Especial|Crédito Suplementar|"
                     r"Alteração da LOA|Alteração da LDO|Lei de Diretrizes Orçamentárias|"
                     r"Lei Orçamentária Anual|Plano Plurianual)\)", re.I)
RE_PRAZO = re.compile(r"Prazo:\s*de\s*(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")
RE_TOTAL = re.compile(r"Total:\s*(\d+)")
RE_RELATOR = re.compile(r"Relator:\s*((?:Senador|Senadora|Deputado|Deputada)[^|\n]*?\([^)]*\))")
RE_VALOR = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b")
RE_LEI = re.compile(r"Lei\s+n[ºo°]\s*([\d.]+)\s*,?\s*de\s*(\d{2}/\d{2}/\d{4})", re.I)

SITUACOES = [
    "AGUARDANDO DESPACHO",
    "MATÉRIA DESPACHADA",
    "AGUARDANDO RECEBIMENTO DE EMENDAS",
    "AGUARDANDO DESIGNAÇÃO DO RELATOR",
    "MATÉRIA COM A RELATORIA",
    "AGUARDANDO LEITURA",
    "PRONTA PARA A PAUTA NA COMISSÃO",
    "PRONTO PARA A PAUTA NA COMISSÃO",
    "PRONTO PARA DELIBERAÇÃO DO PLENÁRIO",
    "PRONTA PARA DELIBERAÇÃO DO PLENÁRIO",
    "TRANSFORMADA EM NORMA JURÍDICA",
    "SEM EFICÁCIA",
    "REJEITADA",
    "PREJUDICADA",
    "ARQUIVADA",
    "DEVOLVIDA",
]


@dataclass
class UnidadeOrcamentaria:
    orgao: str
    unidade: str
    valor: float


@dataclass
class Proposta:
    identificacao: str
    especie: str            # MPV | PLN
    numero: int
    ano: int
    tipo: str               # Crédito Extraordinário, Crédito Especial, ...
    ementa: str
    valor_total: float | None
    relator: str | None
    emendas_inicio: str | None      # ISO
    emendas_fim: str | None         # ISO
    emendas_total: int | None
    situacao: str | None
    norma: str | None
    codigo_materia: str | None
    url_materia: str | None
    unidades: list[UnidadeOrcamentaria] = field(default_factory=list)


def _num(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", "."))


def _iso(br: str) -> str:
    d, m, a = br.split("/")
    return f"{a}-{m}-{d}"


def _limpa(txt: str) -> str:
    return re.sub(r"\s+", " ", txt or "").strip()


def baixar(ano: int, tentativas: int = 3) -> str:
    """Baixa o HTML da página para um ano de apresentação.

    A página aceita o ano por querystring; se o parâmetro for ignorado pelo
    portal, a resposta cai no ano corrente e o filtro por ano é aplicado
    depois, na consolidação.
    """
    erro = None
    for i in range(tentativas):
        try:
            r = requests.get(URL, params={"ano": ano}, headers=HEADERS, timeout=90)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001
            erro = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"falha ao baixar propostas de {ano}: {erro}")


def _unidades_da_linha(tr) -> list[UnidadeOrcamentaria]:
    """Lê a tabela aninhada com Órgão / Unidade Orçamentária / Valor."""
    unidades: list[UnidadeOrcamentaria] = []
    for tabela in tr.find_all("table"):
        cabecalho = _limpa(tabela.get_text(" ")).lower()
        if "unidade" not in cabecalho:
            continue
        for linha in tabela.find_all("tr"):
            celulas = [_limpa(td.get_text(" ")) for td in linha.find_all(["td", "th"])]
            if len(celulas) < 3:
                continue
            if "unidade" in celulas[1].lower() and "valor" in celulas[2].lower():
                continue  # cabeçalho
            m = RE_VALOR.search(celulas[2])
            if not m:
                continue
            unidades.append(UnidadeOrcamentaria(
                orgao=celulas[0], unidade=celulas[1], valor=_num(m.group(0))
            ))
    return unidades


def _situacao_do_texto(texto: str) -> str | None:
    achadas = [(texto.rfind(s), s) for s in SITUACOES if s in texto]
    achadas = [(p, s) for p, s in achadas if p >= 0]
    if not achadas:
        return None
    # a situação aparece no fim da linha; em caso de sobreposição, a mais longa vence
    pos = max(p for p, _ in achadas)
    candidatas = [s for p, s in achadas if p == pos]
    return max(candidatas, key=len)


def parsear(html: str) -> list[Proposta]:
    sopa = BeautifulSoup(html, "html.parser")
    propostas: list[Proposta] = []
    vistos: set[str] = set()

    for link in sopa.find_all("a", href=True):
        rotulo = _limpa(link.get_text(" "))
        ident = RE_IDENT.match(rotulo)
        if not ident:
            continue
        if not RE_MATERIA.search(link["href"]):
            continue

        tr = link.find_parent("tr")
        if tr is None:
            continue
        # a linha do registro é a mais externa que contém este link
        while tr.find_parent("tr") is not None:
            tr = tr.find_parent("tr")

        especie, numero, ano = ident.group(1).upper(), int(ident.group(2)), int(ident.group(3))
        chave = f"{especie} {numero}/{ano}"
        if chave in vistos:
            continue
        vistos.add(chave)

        texto = _limpa(tr.get_text(" "))
        codigo = RE_MATERIA.search(link["href"]).group(1)

        # ementa: o texto logo após "(Tipo)" na primeira célula
        celulas = tr.find_all("td", recursive=False) or tr.find_all("td")
        primeira = _limpa(celulas[0].get_text(" ")) if celulas else texto
        tipo_m = RE_TIPO.search(primeira) or RE_TIPO.search(texto)
        tipo = tipo_m.group(1) if tipo_m else ""
        ementa = ""
        if tipo_m and tipo_m.re is RE_TIPO:
            depois = primeira[primeira.find(tipo_m.group(0)) + len(tipo_m.group(0)):]
            ementa = _limpa(depois)
        ementa = re.sub(r"^(Crédito\s+[Ee]xtraordinári[oa]|Crédito\s+[Ee]special|"
                        r"Crédito\s+[Ss]uplementar)\s*[-–—]\s*", "", ementa).strip()

        unidades = _unidades_da_linha(tr)

        valor_total = None
        soma_uo = sum(u.valor for u in unidades)
        valores = [_num(v) for v in RE_VALOR.findall(texto)]
        if valores:
            # o valor total é o maior da linha e costuma bater com a soma das UOs
            valor_total = max(valores)
        if soma_uo and (valor_total is None or abs(valor_total - soma_uo) > 1):
            valor_total = round(soma_uo, 2)

        prazo = RE_PRAZO.search(texto)
        total = RE_TOTAL.search(texto)
        relator = RE_RELATOR.search(texto)
        lei = RE_LEI.search(texto)

        propostas.append(Proposta(
            identificacao=chave,
            especie=especie,
            numero=numero,
            ano=ano,
            tipo=tipo,
            ementa=ementa,
            valor_total=valor_total,
            relator=_limpa(relator.group(1)) if relator else None,
            emendas_inicio=_iso(prazo.group(1)) if prazo else None,
            emendas_fim=_iso(prazo.group(2)) if prazo else None,
            emendas_total=int(total.group(1)) if total else None,
            situacao=_situacao_do_texto(texto),
            norma=f"Lei nº {lei.group(1)} de {lei.group(2)}" if lei else None,
            codigo_materia=codigo,
            url_materia=link["href"] if link["href"].startswith("http")
            else "https://www25.senado.leg.br" + link["href"],
            unidades=unidades,
        ))

    return propostas


def coletar(anos: list[int]) -> list[Proposta]:
    todas: dict[str, Proposta] = {}
    for ano in anos:
        html = baixar(ano)
        achadas = parsear(html)
        print(f"  {ano}: {len(achadas)} propostas na página", file=sys.stderr)
        for p in achadas:
            todas.setdefault(p.identificacao, p)
        time.sleep(1.5)
    return list(todas.values())


def main() -> None:
    anos = [int(a) for a in sys.argv[1:]] or [2026]
    propostas = coletar(anos)
    saida = Path("dados/congresso.json")
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(
        json.dumps([asdict(p) for p in propostas], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    mpvs = [p for p in propostas if p.tipo.lower().startswith("crédito extraordin")]
    print(f"{len(propostas)} propostas ({len(mpvs)} créditos extraordinários) -> {saida}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
