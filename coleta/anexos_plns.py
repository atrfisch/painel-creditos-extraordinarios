"""
Anexos dos PLNs de crédito suplementar e especial.

Reaproveita toda a maquinaria de `anexos.py` — extração de PDF, OCR com
validação, nova tentativa quando falha — mudando duas coisas:

- **o endereço da página**. PLN não tem a rota de medida provisória; a tabela do
  Congresso aponta para a página de matéria do Senado, e há também a rota de
  projetos de lei no portal do Congresso. As duas são tentadas.
- **os prazos**. PLN não caduca, então não se busca janela de deliberação nem
  regime de urgência. Interessam a apresentação, o despacho e o prazo de
  emendas.

O resultado vai para `config/anexos_plns.json`, separado do cache das MPs.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from anexos import (RE_DESPACHO, RE_MSG, RE_SITUACAO_PRAZO, ROTULO_EMENDAS,
                    RE_UNIDADE, _iso, _janela, _limpa, baixar,
                    documentos_da_materia, ordenar_documentos,
                    parsear_anexo, texto_do_pdf)

CACHE = Path("config/anexos_plns.json")

# Rotas possíveis para a página de um PLN, da mais informativa para a menos.
ROTAS = [
    "https://www.congressonacional.leg.br/materias/pesquisa/-/materia/{codigo}",
    "https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo}",
]

RE_APRESENTACAO = re.compile(
    r"(?:Data de [Aa]presentação|Apresentação|Data)\s*:?\s*(\d{2}/\d{2}/\d{4})")
RE_AUTOR = re.compile(r"Autor\s*:?\s*(Presidência da República|Poder Executivo|[^:\n]{3,50}?)"
                      r"\s*(?:Data|Descrição|Local|$)")
RE_LOCAL = re.compile(r"Local\s*:?\s*([^:\n]{3,80}?)\s*(?:Ação Legislativa|Autor|Data|$)")
RE_ACAO_LEG = re.compile(r"Ação Legislativa\s*:?\s*(.{20,300}?)(?:\s*\|\s*Veja a tramitação|$)")
RE_LEI = re.compile(r"Lei\s+n[ºo°]\s*([\d.]+)[,\s]*de\s*(\d{2}/\d{2}/\d{4})", re.I)


def ler_pagina(codigo: str) -> dict:
    """Metadados e documentos do PLN, tentando as rotas conhecidas."""
    melhor: dict = {}
    for rota in ROTAS:
        url = rota.format(codigo=codigo)
        try:
            html = baixar(url)
        except Exception:  # noqa: BLE001
            continue

        sopa = BeautifulSoup(html, "html.parser")
        texto = _limpa(sopa.get_text(" "))

        e_ini, e_fim = _janela(texto, ROTULO_EMENDAS)
        apres = RE_APRESENTACAO.search(texto)
        desp = RE_DESPACHO.search(texto)
        sit = RE_SITUACAO_PRAZO.search(texto)
        msg = RE_MSG.search(texto)
        lei = RE_LEI.search(texto)

        candidatos = []
        for a in sopa.find_all("a", href=True):
            if "sdleg-getter" not in a["href"]:
                continue
            candidatos.append({
                "url": a["href"],
                "rotulo": _limpa(a.get_text(" ")) or _limpa(a.get("title", "")),
                "origem": "página",
            })
        # Os Dados Abertos listam documentos que a página nem sempre expõe como
        # link com rótulo reconhecível — foi o que faltou no PLN 15.
        candidatos = ordenar_documentos(
            documentos_da_materia(codigo) + candidatos,
            ("projeto de lei", "pln", "avulso", "texto"))

        registro = {
            "url_pagina": url,
            "autor": _limpa(autor.group(1)) if autor else None,
            "local": _limpa(local.group(1)) if local else None,
            "acao_legislativa": _limpa(acao_leg.group(1))[:280] if acao_leg else None,
            "apresentacao": _iso(apres.group(1)) if apres else None,
            "despacho": _iso(desp.group(1)) if desp else None,
            "emendas_inicio_oficial": _iso(e_ini),
            "emendas_fim_oficial": _iso(e_fim),
            "situacao_prazo": _limpa(sit.group(1)).capitalize() if sit else None,
            "mensagem": _limpa(msg.group(1)) if msg else None,
            "norma": f"Lei nº {lei.group(1)} de {lei.group(2)}" if lei else None,
            "candidatos": candidatos[:6],
        }
        # fica com a rota que trouxe documentos; se nenhuma trouxer, com a
        # primeira que ao menos respondeu
        if candidatos:
            return registro
        melhor = melhor or registro
        time.sleep(0.8)
    return melhor


def coletar(plns: list[dict], cache: dict) -> dict:
    """Mesma política das MPs: falha não vira cache, é tentada de novo."""
    hoje = date.today().isoformat()

    for p in plns:
        ident, codigo = p["identificacao"], p.get("codigo_materia")
        if not codigo:
            continue
        anterior = cache.get(ident)
        if anterior and anterior.get("programatica"):
            continue
        if anterior and anterior.get("tentado_em") == hoje:
            continue

        try:
            registro = ler_pagina(codigo) or {}
            registro["tentado_em"] = hoje
            registro["tentativas"] = (anterior or {}).get("tentativas", 0) + 1
            registro["programatica"] = []
            esperado = p.get("valor_total")

            for cand in (registro.get("candidatos") or [])[:6]:
                try:
                    bruto = baixar(cand["url"], binario=True)
                except Exception as e:  # noqa: BLE001
                    print(f"    {cand['rotulo'][:40]}: download falhou ({e})", file=sys.stderr)
                    continue
                conteudo, metodo = texto_do_pdf(bruto)
                if not RE_UNIDADE.search(conteudo or ""):
                    continue
                linhas = parsear_anexo(conteudo)
                if not linhas:
                    continue
                total = round(sum(l["valor"] for l in linhas), 2)
                # o Anexo II traz as dotações anuladas para financiar o crédito
                cancelamento = parsear_anexo(conteudo, secao="cancelamento")
                if esperado and abs(total - esperado) > 1:
                    print(f"    {cand['rotulo'][:40]} [{metodo}]: total {total:,.0f} "
                          f"≠ {esperado:,.0f} — descartado", file=sys.stderr)
                    continue
                registro["programatica"] = linhas
                registro["cancelamento"] = cancelamento
                registro["total_cancelamento"] = round(
                    sum(l["valor"] for l in cancelamento), 2)
                registro["total_anexo"] = total
                registro["metodo"] = metodo
                registro["documento"] = cand["url"]
                print(f"  {ident}: {len(linhas)} linha(s) de suplementação"
                      + (f", {len(cancelamento)} de cancelamento" if cancelamento else "")
                      + f" [{metodo}]", file=sys.stderr)
                break

            if not registro["programatica"]:
                cands = registro.get("candidatos") or []
                fontes = ", ".join(sorted({c.get("origem", "?") for c in cands})) or "nenhuma"
                print(f"::warning::{ident}: anexo não obtido — {len(cands)} documento(s) "
                      f"testado(s) (fontes: {fontes}). Será tentado de novo", file=sys.stderr)
                for c in cands[:4]:
                    print(f"    testado: {c.get('rotulo','')[:50]} [{c.get('origem','?')}]",
                          file=sys.stderr)
            cache[ident] = registro
        except Exception as e:  # noqa: BLE001
            print(f"  {ident}: falhou ({e})", file=sys.stderr)
        time.sleep(1.2)

    return cache


TIPOS = ("crédito suplementar", "crédito especial")


def main() -> None:
    propostas = json.loads(Path("dados/congresso.json").read_text(encoding="utf-8"))
    plns = [p for p in propostas
            if p.get("especie") == "PLN" and p.get("tipo", "").lower() in TIPOS]
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    cache = coletar(plns, cache)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    uos = sorted({l["uo_codigo"] for v in cache.values() for l in v.get("programatica", [])})
    if uos:
        arq = Path("dados/uos_plns.txt")
        arq.parent.mkdir(exist_ok=True)
        arq.write_text("\n".join(uos), encoding="utf-8")
    com = sum(1 for v in cache.values() if v.get("programatica"))
    pendentes = [k for k, v in cache.items() if not v.get("programatica")]
    print(f"anexos de PLN: {len(cache)} em cache, {com} com programática, "
          f"{len(uos)} unidades", file=sys.stderr)
    if pendentes:
        print(f"  pendentes: {', '.join(sorted(pendentes))}", file=sys.stderr)


if __name__ == "__main__":
    main()
