"""
Anexo da MP: a fonte exata da programática do crédito.

A tabela de propostas do Congresso traz a unidade orçamentária pelo nome e não
traz a ação. O anexo enviado pela Presidência traz os dois pelo código:

    ÓRGÃO: 33000 - Ministério da Previdência Social
    UNIDADE: 33201 - Instituto Nacional do Seguro Social – INSS
    2314 00XK 6500 Ressarcimento aos Beneficiários ... 09 271 547.000.000
         S 3-ODC 1 90 0 3049 547.000.000

Com o par unidade + ação o cruzamento com o SIOP deixa de ser aproximação.

A mesma página traz os prazos oficiais — publicação no DOU, janela de
deliberação (a vigência) e janela de emendas —, que substituem o cálculo do
art. 62 feito em `vigencia.py`. Vale confiar neles: para a MPV 1378/2026, o
Congresso contou 21/07 a 18/09 corridos, sem suspender pelo recesso de julho,
enquanto o prazo de emendas foi suspenso. A regra na prática é menos direta que
o texto constitucional sugere.

O resultado é gravado em `config/anexos.json`, que funciona como cache: MPs já
processadas não são baixadas de novo.
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PAGINA_MP = "https://www.congressonacional.leg.br/materias/medidas-provisorias/-/mpv/{codigo}"
HEADERS = {
    "User-Agent": "painel-creditos-extraordinarios/2.0 (+github pages)",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
CACHE = Path("config/anexos.json")

RE_ORGAO = re.compile(r"ÓRGÃO\s*:\s*(\d{4,5})\s*[-–—]\s*([^\n]+)")
RE_UNIDADE = re.compile(r"UNIDADE\s*:\s*(\d{4,6})\s*[-–—]\s*([^\n]+)")
# programa | ação | localizador | descrição | função | subfunção | valor
RE_ACAO = re.compile(
    r"\b(\d{4})\s+([0-9A-Z]{4})\s+(\d{4})\s+(.{0,220}?)\s+(\d{2})\s+(\d{3})\s+"
    r"(\d{1,3}(?:\.\d{3})+|\d{4,})\b"
)
# esfera | GND | resultado primário | modalidade | IU | fonte | valor
RE_QUALIF = re.compile(
    r"\b([SF])\s+(\d)-([A-Z]{3})\s+(\d)\s+(\d{2})\s+(\d)\s+(\d{3,4})\s+"
    r"(\d{1,3}(?:\.\d{3})+|\d{4,})\b"
)

RE_DOU = re.compile(r"Publicação no DOU\s*:?\s*(\d{2}/\d{2}/\d{4})")
RE_DELIB = re.compile(
    r"Deliberação da Medida Provisória\s*:?\s*(?:de\s*)?(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")
RE_EMENDAS = re.compile(
    r"Apresentação de emendas\s*:?\s*(?:de\s*)?(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})",
    re.I)
RE_URGENCIA = re.compile(r"Regime de urgência,? a partir de\s*:?\s*(\d{2}/\d{2}/\d{4})")
RE_ATO = re.compile(r"Ato do Presidente da Mesa do Congresso Nacional n[ºo°]?\s*([\d./-]+)", re.I)


def _iso(br: str | None) -> str | None:
    if not br:
        return None
    d, m, a = br.split("/")
    return f"{a}-{m}-{d}"


def _num(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", "."))


def _limpa(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def baixar(url: str, binario: bool = False, tentativas: int = 3):
    erro = None
    for i in range(tentativas):
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            if binario:
                return r.content
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001
            erro = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"falha ao baixar {url}: {erro}")


def ler_pagina(codigo: str) -> dict:
    """Prazos oficiais e link do documento da Presidência."""
    html = baixar(PAGINA_MP.format(codigo=codigo))
    sopa = BeautifulSoup(html, "html.parser")
    texto = _limpa(sopa.get_text(" "))

    delib = RE_DELIB.search(texto)
    emendas = RE_EMENDAS.search(texto)
    dou = RE_DOU.search(texto)
    urgencia = RE_URGENCIA.search(texto)

    documento = None
    for a in sopa.find_all("a", href=True):
        if "sdleg-getter" not in a["href"]:
            continue
        rotulo = _limpa(a.get_text(" ")).lower()
        titulo = _limpa(a.get("title", "")).lower()
        if rotulo.startswith("medida provisória") or titulo.startswith("mpv "):
            documento = a["href"]
            break
    if not documento:  # o avulso inicial contém o mesmo anexo
        for a in sopa.find_all("a", href=True):
            if "sdleg-getter" in a["href"] and "avulso" in _limpa(a.get_text(" ")).lower():
                documento = a["href"]
                break

    # A janela de deliberação publicada é a do período corrente: 60 dias no
    # início e cerca de 120 depois da prorrogação, que o art. 62, § 7º torna
    # automática. O tamanho da janela é o indicador mais confiável de que a
    # prorrogação já ocorreu; o Ato da Mesa, quando citado, confirma.
    inicio = _iso(delib.group(1)) if delib else None
    fim = _iso(delib.group(2)) if delib else None
    prorrogada, ato = False, None
    if inicio and fim:
        dias = (date.fromisoformat(fim) - date.fromisoformat(inicio)).days + 1
        prorrogada = dias > 90
    if "prorrog" in texto.lower():
        m = RE_ATO.search(texto)
        ato = m.group(1) if m else None
        if ato:
            prorrogada = True

    return {
        "publicacao_dou": _iso(dou.group(1)) if dou else None,
        "deliberacao_inicio": inicio,
        "deliberacao_fim": fim,
        "prorrogada": prorrogada,
        "ato_prorrogacao": ato,
        "emendas_inicio_oficial": _iso(emendas.group(1)) if emendas else None,
        "emendas_fim_oficial": _iso(emendas.group(2)) if emendas else None,
        "urgencia": _iso(urgencia.group(1)) if urgencia else None,
        "documento": documento,
    }


def parsear_anexo(texto: str) -> list[dict]:
    """Extrai as linhas de programática do texto do PDF.

    O texto do PDF quebra descrições em várias linhas, então o bloco de cada
    unidade é normalizado para uma linha só antes das expressões regulares.
    """
    marcas = []
    for m in RE_ORGAO.finditer(texto):
        marcas.append(("orgao", m.start(), m.group(1), _limpa(m.group(2))))
    for m in RE_UNIDADE.finditer(texto):
        marcas.append(("unidade", m.start(), m.group(1), _limpa(m.group(2))))
    marcas.sort(key=lambda x: x[1])

    linhas: list[dict] = []
    orgao_atual = ("", "")
    for i, (tipo, pos, codigo, nome) in enumerate(marcas):
        if tipo == "orgao":
            orgao_atual = (codigo, nome)
            continue
        fim = marcas[i + 1][1] if i + 1 < len(marcas) else len(texto)
        bloco = _limpa(texto[pos:fim])

        for acao in RE_ACAO.finditer(bloco):
            programa, cod_acao, localizador, descricao, funcao, subfuncao, valor = acao.groups()
            qualif = RE_QUALIF.search(bloco, acao.end(), acao.end() + 400)
            linhas.append({
                "orgao_codigo": orgao_atual[0],
                "orgao": orgao_atual[1],
                "uo_codigo": codigo.zfill(5),
                "unidade": nome,
                "programa": programa,
                "acao": cod_acao,
                "localizador": localizador,
                "descricao": _limpa(descricao),
                "funcao": funcao,
                "subfuncao": subfuncao,
                "esfera": qualif.group(1) if qualif else None,
                "gnd": f"{qualif.group(2)}-{qualif.group(3)}" if qualif else None,
                "rp": qualif.group(4) if qualif else None,
                "modalidade": qualif.group(5) if qualif else None,
                "fonte": qualif.group(7) if qualif else None,
                "valor": _num(valor),
            })
    return linhas


def texto_do_pdf(conteudo: bytes) -> str:
    """Texto do PDF, tentando mais de um arranjo de layout.

    O anexo é uma tabela, e extratores diferentes intercalam as colunas de
    formas diferentes. Se o modo padrão não produzir as marcas esperadas, vale
    tentar o modo com layout preservado antes de desistir.
    """
    import pdfplumber

    def extrair(**kwargs) -> str:
        partes = []
        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            for pagina in pdf.pages:
                partes.append(pagina.extract_text(**kwargs) or "")
        return "\n".join(partes)

    texto = extrair()
    if not RE_UNIDADE.search(texto or ""):
        alternativo = extrair(layout=True)
        if RE_UNIDADE.search(alternativo or ""):
            return alternativo
    return texto


def coletar(mpvs: list[dict], cache: dict) -> dict:
    for p in mpvs:
        ident, codigo = p["identificacao"], p.get("codigo_materia")
        if not codigo or ident in cache:
            continue
        try:
            registro = ler_pagina(codigo)
            if registro.get("documento"):
                bruto = baixar(registro["documento"], binario=True)
                registro["programatica"] = parsear_anexo(texto_do_pdf(bruto))
            else:
                registro["programatica"] = []
            registro["total_anexo"] = round(
                sum(l["valor"] for l in registro["programatica"]), 2)
            cache[ident] = registro
            n = len(registro["programatica"])
            if n == 0 and registro.get("documento"):
                print(f"::warning::{ident}: anexo baixado mas sem programática legível",
                      file=sys.stderr)
                despejo = Path("dados") / f"anexo_{ident.replace('/', '-').replace(' ', '_')}.txt"
                despejo.parent.mkdir(exist_ok=True)
                try:
                    despejo.write_text(texto_do_pdf(bruto)[:20000], encoding="utf-8")
                    print(f"    texto salvo em {despejo} para inspeção", file=sys.stderr)
                except Exception:  # noqa: BLE001
                    pass
            aviso = ""
            if p.get("valor_total") and registro["total_anexo"]:
                dif = abs(registro["total_anexo"] - p["valor_total"])
                if dif > 1:
                    aviso = f"  ⚠ anexo soma {registro['total_anexo']:,.0f}, tabela {p['valor_total']:,.0f}"
            print(f"  {ident}: {n} linha(s) de programática{aviso}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  {ident}: falhou ({e})", file=sys.stderr)
        time.sleep(1.2)
    return cache


def main() -> None:
    propostas = json.loads(Path("dados/congresso.json").read_text(encoding="utf-8"))
    mpvs = [p for p in propostas if p.get("tipo", "").lower().startswith("crédito extraordin")]
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    antes = len(cache)
    cache = coletar(mpvs, cache)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    uos = sorted({l["uo_codigo"] for v in cache.values() for l in v.get("programatica", [])})
    Path("dados").mkdir(exist_ok=True)
    Path("dados/uos.txt").write_text("\n".join(uos), encoding="utf-8")

    com_prog = sum(1 for v in cache.values() if v.get("programatica"))
    print(f"  {len(uos)} unidades orçamentárias -> dados/uos.txt", file=sys.stderr)
    if not uos:
        print("::error::nenhuma programática extraída dos anexos — sem os códigos de "
              "unidade o cruzamento com o SIOP não tem como acontecer", file=sys.stderr)
    print(f"anexos: {len(cache)} em cache ({len(cache) - antes} novos), "
          f"{com_prog} com programática", file=sys.stderr)


if __name__ == "__main__":
    main()
