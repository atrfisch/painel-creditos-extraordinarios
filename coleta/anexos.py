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
# O valor exige grupos de milhar bem formados e nada de dígito ou ponto logo
# depois. Sem o lookahead, um "985.600.0000" mal lido pelo OCR casaria como
# 985.600 e entraria no painel truncado; assim ele simplesmente não casa, a
# linha cai fora e a conferência do total acusa.
VALOR = r"\d{1,3}(?:\.\d{3})+(?![\d.])|\d{4,}(?![\d.])"
RE_ACAO = re.compile(
    r"\b(\d{4})\s+([0-9A-Z]{4})\s+(\d{4})\s+(.{0,220}?)\s+(\d{2})\s+(\d{3})\s+"
    r"(" + VALOR + r")"
)
# esfera | GND | resultado primário | modalidade | IU | fonte | valor
RE_QUALIF = re.compile(
    r"\b([SF])\s+(\d)-([A-Z]{3})\s+(\d)\s+(\d{2})\s+(\d)\s+(\d{3,4})\s+"
    r"(" + VALOR + r")"
)

RE_DOU = re.compile(r"Publicação no DOU\s*:?\s*(\d{2}/\d{2}/\d{4})")

# A página traz os prazos em duas disposições e nem sempre nas duas ao mesmo
# tempo. Em "Prazos abertos" as datas vêm antes do rótulo; na seção
# "Calendário", depois. Só a segunda lista os prazos já encerrados, então é ela
# que vale quando existe — mas para MPs recentes às vezes só há a primeira.
def _janela(texto: str, rotulo: str) -> tuple[str | None, str | None]:
    depois = re.search(
        rotulo + r"\s*:?\s*(?:de\s*)?(\d{2}/\d{2}/\d{4})\s*(?:a|-|–|até)\s*(\d{2}/\d{2}/\d{4})",
        texto, re.I)
    if depois:
        return depois.group(1), depois.group(2)
    antes = re.search(
        r"(\d{2}/\d{2}/\d{4})\s*(?:a|-|–|até)\s*(\d{2}/\d{2}/\d{4})\s*:\s*" + rotulo,
        texto, re.I)
    if antes:
        return antes.group(1), antes.group(2)
    return None, None


ROTULO_DELIB = r"Deliberação da Medida Provisória"
ROTULO_EMENDAS = r"Apresentação de [Ee]mendas(?:\s+à Medida Provisória)?"
RE_URGENCIA = re.compile(
    r"(?:Regime de [Uu]rgência,?\s*a partir de\s*:?\s*(\d{2}/\d{2}/\d{4})"
    r"|Regime de [Uu]rgência\s*(\d{2}/\d{2}/\d{4})\s*em diante"
    r"|(\d{2}/\d{2}/\d{4})\s*em diante)")
RE_ATO = re.compile(r"Ato do Presidente da Mesa do Congresso Nacional n[ºo°]?\s*([\d./-]+)", re.I)
RE_SITUACAO_PRAZO = re.compile(r"Situação do prazo\s*:?\s*(Aberto|Encerrado|Suspenso)", re.I)
RE_ULTIMO_ESTADO = re.compile(r"Último estado\s*:?\s*([A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][^\n|]{3,80}?)\s*(?:Prazos|Calendário|$)")
RE_DESPACHO = re.compile(r"Despacho\s*:?\s*(\d{2}/\d{2}/\d{4})")
RE_NUM_CAMARA = re.compile(r"Número na Câmara\s*:?\s*(MPV?\s*\d+/\d{4})", re.I)
RE_MSG = re.compile(r"Origem externa\s*:?\s*(MSG\s*\d+/\d{4})", re.I)


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

    d_ini, d_fim = _janela(texto, ROTULO_DELIB)
    e_ini, e_fim = _janela(texto, ROTULO_EMENDAS)
    dou = RE_DOU.search(texto)
    urg = RE_URGENCIA.search(texto)
    urgencia = next((g for g in urg.groups() if g), None) if urg else None

    # Todos os documentos da página, em ordem de preferência. Guardar só o
    # primeiro que casa com um rótulo estreito deixava de fora MPs cujo rótulo
    # varia; e quando o PDF escolhido vem sem camada de texto, é preciso ter
    # outro a tentar. A coleta percorre a lista até um deles render programática.
    def prioridade(rotulo: str, titulo: str) -> int:
        r = f"{rotulo} {titulo}".lower()
        if r.startswith("medida provisória") or r.startswith("mpv "):
            return 0
        if "medida provisória" in r or re.search(r"\bmpv\b", r):
            return 1
        if "avulso" in r or "texto" in r or "integral" in r:
            return 2
        if "mensagem" in r or "msg" in r:
            return 4
        return 3

    vistos, candidatos = set(), []
    for a in sopa.find_all("a", href=True):
        if "sdleg-getter" not in a["href"] or a["href"] in vistos:
            continue
        vistos.add(a["href"])
        rotulo = _limpa(a.get_text(" "))
        candidatos.append({
            "url": a["href"],
            "rotulo": rotulo or _limpa(a.get("title", "")),
            "ordem": prioridade(rotulo, _limpa(a.get("title", ""))),
        })
    candidatos.sort(key=lambda c: c["ordem"])
    documento = candidatos[0]["url"] if candidatos else None

    # A janela de deliberação publicada é a do período corrente: 60 dias no
    # início e cerca de 120 depois da prorrogação, que o art. 62, § 7º torna
    # automática. O tamanho da janela é o indicador mais confiável de que a
    # prorrogação já ocorreu; o Ato da Mesa, quando citado, confirma.
    inicio, fim = _iso(d_ini), _iso(d_fim)

    # O número do Ato da Mesa só é aproveitado quando "prorrog" aparece perto
    # dele. A palavra ocorre solta em quase toda página — no art. 62 citado no
    # despacho, em títulos de seção — e tomá-la como sinal marcava MPs como
    # prorrogadas sem terem sido, o que fazia a caducidade colapsar para o fim
    # do primeiro período. Quem decide se houve prorrogação é vigencia.py, pela
    # posição da janela publicada; aqui só se guarda a referência.
    ato = None
    for m in RE_ATO.finditer(texto):
        vizinhanca = texto[max(0, m.start() - 250):m.end() + 250].lower()
        if "prorrog" in vizinhanca:
            ato = m.group(1)
            break

    sit = RE_SITUACAO_PRAZO.search(texto)
    desp = RE_DESPACHO.search(texto)
    camara = RE_NUM_CAMARA.search(texto)
    msg = RE_MSG.search(texto)

    return {
        "situacao_prazo": _limpa(sit.group(1)).capitalize() if sit else None,
        "despacho": _iso(desp.group(1)) if desp else None,
        "numero_camara": _limpa(camara.group(1)) if camara else None,
        "mensagem": _limpa(msg.group(1)) if msg else None,
        "publicacao_dou": _iso(dou.group(1)) if dou else None,
        "deliberacao_inicio": inicio,
        "deliberacao_fim": fim,
        "ato_prorrogacao": ato,
        "emendas_inicio_oficial": _iso(e_ini),
        "emendas_fim_oficial": _iso(e_fim),
        "urgencia": _iso(urgencia),
        "documento": documento,
        "candidatos": candidatos[:6],
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


def _corrigir_codigos(texto: str) -> str:
    """Desfaz confusões típicas de OCR nos campos de código.

    Os códigos de ação do SIOP não usam as letras O e I justamente para evitar
    ambiguidade com 0 e 1, então trocar de volta é seguro. A correção é aplicada
    só em blocos de quatro caracteres cercados por espaço, para não estragar
    palavras da descrição.
    """
    def trocar(m: re.Match) -> str:
        codigo = m.group(0)
        return codigo.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"}))

    return re.sub(r"(?<= )[0-9OIl][0-9A-Za-z]{3}(?= )", trocar, texto)


def texto_do_pdf(conteudo: bytes, permitir_ocr: bool = True) -> tuple[str, str]:
    """Texto do PDF e o método que o produziu.

    O anexo é uma tabela, e extratores diferentes intercalam as colunas de
    formas diferentes — daí a segunda tentativa com layout preservado.

    Alguns anexos, sobretudo os das MPs recém-publicadas, vêm como digitalização
    sem camada de texto: o PDF é imagem, e não há nada a extrair. Nesses casos
    entra o OCR, cujo resultado é sempre conferido contra o valor total que a
    tabela do Congresso informa antes de ser aproveitado.
    """
    import pdfplumber

    def extrair(**kwargs) -> str:
        partes = []
        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            for pagina in pdf.pages:
                partes.append(pagina.extract_text(**kwargs) or "")
        return "\n".join(partes)

    texto = extrair()
    if RE_UNIDADE.search(texto or ""):
        return texto, "texto"

    alternativo = extrair(layout=True)
    if RE_UNIDADE.search(alternativo or ""):
        return alternativo, "texto (layout)"

    if permitir_ocr:
        try:
            return _ocr(conteudo), "ocr"
        except Exception as e:  # noqa: BLE001
            print(f"    OCR indisponível ({e})", file=sys.stderr)

    return texto, "vazio"


def _ocr(conteudo: bytes) -> str:
    """Rasteriza o PDF e passa o tesseract, página a página."""
    import subprocess
    import tempfile

    import pytesseract
    from PIL import Image

    idiomas = ""
    try:
        disponiveis = pytesseract.get_languages(config="")
        if "por" in disponiveis:
            idiomas = "por"
        elif "eng" in disponiveis:
            idiomas = "eng"
    except Exception:  # noqa: BLE001
        idiomas = "eng"

    with tempfile.TemporaryDirectory() as tmp:
        origem = Path(tmp) / "anexo.pdf"
        origem.write_bytes(conteudo)
        subprocess.run(["pdftoppm", "-r", "300", "-png", str(origem), f"{tmp}/pg"],
                       check=True, capture_output=True, timeout=300)
        partes = []
        for png in sorted(Path(tmp).glob("pg*.png")):
            partes.append(pytesseract.image_to_string(
                Image.open(png), lang=idiomas or None, config="--psm 6"))
    return _corrigir_codigos("\n".join(partes))


def coletar(mpvs: list[dict], cache: dict) -> dict:
    """Baixa página e anexo de cada MP que ainda não tem programática válida.

    Uma coleta sem programática NÃO é gravada como definitiva: fica marcada
    como pendente e é tentada de novo na execução seguinte. Isso importa porque
    o PDF de uma MP recém-publicada costuma ser digitalização sem camada de
    texto, e a versão com texto aparece dias depois — se a falha virasse cache,
    a MP ficaria para sempre sem anexo mesmo quando o documento bom chegasse.
    """
    hoje = date.today().isoformat()

    for p in mpvs:
        ident, codigo = p["identificacao"], p.get("codigo_materia")
        if not codigo:
            continue

        anterior = cache.get(ident)
        if anterior and anterior.get("programatica"):
            continue  # já resolvida
        if anterior and anterior.get("tentado_em") == hoje:
            continue  # já tentada hoje, não insiste na mesma execução

        try:
            registro = ler_pagina(codigo)
            registro["tentado_em"] = hoje
            registro["tentativas"] = (anterior or {}).get("tentativas", 0) + 1
            registro["programatica"] = []

            esperado = p.get("valor_total")
            tentativas = registro.get("candidatos") or (
                [{"url": registro["documento"], "rotulo": "documento"}]
                if registro.get("documento") else [])

            for cand in tentativas:
                try:
                    bruto = baixar(cand["url"], binario=True)
                except Exception as e:  # noqa: BLE001
                    print(f"    {cand['rotulo'][:40]}: download falhou ({e})", file=sys.stderr)
                    continue

                texto, metodo = texto_do_pdf(bruto)
                linhas = parsear_anexo(texto)
                if not linhas:
                    print(f"    {cand['rotulo'][:40]} [{metodo}]: sem programática legível",
                          file=sys.stderr)
                    continue

                total = round(sum(l["valor"] for l in linhas), 2)
                # O total do anexo tem de bater com o valor que a tabela do
                # Congresso publica. É o que impede um OCR mal lido de virar
                # número no painel: sem conferência, um dígito trocado passaria.
                if esperado and abs(total - esperado) > 1:
                    print(f"    {cand['rotulo'][:40]} [{metodo}]: total {total:,.0f} "
                          f"≠ {esperado:,.0f} da tabela — descartado", file=sys.stderr)
                    if metodo == "ocr":
                        registro["ocr_divergente"] = True
                    continue

                registro["programatica"] = linhas
                registro["total_anexo"] = total
                registro["metodo"] = metodo
                registro["documento"] = cand["url"]
                print(f"  {ident}: {len(linhas)} linha(s) de programática [{metodo}]",
                      file=sys.stderr)
                break

            if not registro["programatica"]:
                n = registro["tentativas"]
                print(f"::warning::{ident}: anexo não obtido em {n} tentativa(s) — "
                      f"{len(tentativas)} documento(s) testado(s). Será tentado de novo "
                      "na próxima execução", file=sys.stderr)
                if registro.get("ocr_divergente"):
                    print(f"    (o OCR leu o anexo mas o total não confere; "
                          "provavelmente o PDF ainda é digitalização)", file=sys.stderr)
                despejo = Path("dados") / f"anexo_{ident.replace('/', '-').replace(' ', '_')}.txt"
                despejo.parent.mkdir(exist_ok=True)
                try:
                    if tentativas:
                        t, _ = texto_do_pdf(baixar(tentativas[0]["url"], binario=True))
                        despejo.write_text(t[:20000], encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass

            cache[ident] = registro
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

    pendentes = [k for k, v in cache.items() if not v.get("programatica")]
    com_prog = sum(1 for v in cache.values() if v.get("programatica"))
    print(f"  {len(uos)} unidades orçamentárias -> dados/uos.txt", file=sys.stderr)
    if not uos:
        print("::error::nenhuma programática extraída dos anexos — sem os códigos de "
              "unidade o cruzamento com o SIOP não tem como acontecer", file=sys.stderr)
    print(f"anexos: {len(cache)} em cache, {com_prog} com programática", file=sys.stderr)
    if pendentes:
        print(f"  pendentes, serão tentadas de novo: {', '.join(sorted(pendentes))}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
