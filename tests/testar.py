"""Testes offline do parser, do cálculo de vigência e do cruzamento. Não usa rede."""

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "coleta"))

import congresso  # noqa: E402

SIOP = """codigo_uo,unidade,codigo_acao,acao,loa,loa_mais_credito,empenhado,liquidado,pago
32263,ANP,00H9,Subvenção a combustíveis,2000000000,6023000000,3100000000,2600000000,2400000000
32263,ANP,2000,Administração da unidade,480000000,480000000,310000000,290000000,280000000
44201,IBAMA,21C0,Prevenção e combate a incêndios,0,194417722,150000000,120000000,110000000
44207,ICMBio,21C1,Fiscalização ambiental,0,143065710,40000000,31000000,28000000
53101,MIDR,22BF,Defesa civil,300000000,566512000,520000000,480000000,460000000
"""


def main() -> int:
    falhas = []
    html = (RAIZ / "tests/fixture.html").read_text(encoding="utf-8")
    propostas = congresso.parsear(html)
    por_id = {p.identificacao: p for p in propostas}

    if len(propostas) != 5:
        falhas.append(f"esperava 5 propostas, achei {len(propostas)}")

    mpv = por_id.get("MPV 1367/2026")
    if not mpv:
        falhas.append("MPV 1367/2026 não encontrada")
    else:
        if mpv.relator != "Deputado Federal Jilmar Tatto (PT/SP)":
            falhas.append(f"relator: {mpv.relator}")
        if len(mpv.unidades) != 2:
            falhas.append(f"UOs: {len(mpv.unidades)}")
        if mpv.emendas_total != 12:
            falhas.append(f"total de emendas: {mpv.emendas_total}")
        if "Crédito extraordinário" in mpv.ementa:
            falhas.append("prefixo do tipo não removido da ementa")

    lei = por_id.get("MPV 1339/2026")
    if lei and not (lei.norma or "").startswith("Lei nº 15.458"):
        falhas.append(f"norma de conversão: {lei.norma}")

    # HTML mal formado não pode fazer um registro absorver o seguinte
    quebrado = congresso.parsear(html.replace("</tbody></table></div>", "</div>", 1))
    a = {p.identificacao: p for p in quebrado}.get("MPV 1381/2026")
    if not a or a.emendas_inicio != "2026-07-31" or len(a.unidades) > 1:
        falhas.append("o corte entre registros não segurou com HTML quebrado")

    # o anexo da MP: programática exata a partir do texto real do PDF
    from anexos import parsear_anexo  # noqa: E402
    prog = parsear_anexo((RAIZ / "tests/anexo_1378.txt").read_text(encoding="utf-8"))
    if len(prog) != 1:
        falhas.append(f"anexo 1378: esperava 1 linha, achei {len(prog)}")
    elif not (prog[0]["uo_codigo"] == "33201" and prog[0]["acao"] == "00XK"
              and prog[0]["valor"] == 547_000_000 and prog[0]["gnd"] == "3-ODC"):
        falhas.append(f"anexo 1378 mal lido: {prog[0]}")

    multi = parsear_anexo((RAIZ / "tests/anexo_multi.txt").read_text(encoding="utf-8"))
    if [l["uo_codigo"] for l in multi] != ["44201", "44207"]:
        falhas.append(f"anexo multi-órgão: {[l['uo_codigo'] for l in multi]}")

    # pipeline
    (RAIZ / "dados").mkdir(exist_ok=True)
    import shutil
    shutil.copy(RAIZ / "tests/anexos_fixture.json", RAIZ / "config/anexos.json")
    (RAIZ / "dados/congresso.json").write_text(
        json.dumps([asdict(p) for p in propostas], ensure_ascii=False, indent=2), encoding="utf-8")
    (RAIZ / "dados/siop_2026.csv").write_text(SIOP, encoding="utf-8")
    for etapa in ("coleta/vigencia.py", "coleta/consolidar.py"):
        r = subprocess.run([sys.executable, etapa], cwd=RAIZ, capture_output=True, text=True)
        if r.returncode != 0:
            falhas.append(f"{etapa}: {r.stderr[-400:]}")

    saida = RAIZ / "site/dados.json"
    if saida.exists():
        dados = json.loads(saida.read_text(encoding="utf-8"))
        med = {m["identificacao"]: m for m in dados["medidas"]}
        if dados["uo_sem_correspondencia"]:
            falhas.append(f"UOs não casadas: {dados['uo_sem_correspondencia']}")

        anp = med.get("MPV 1381/2026", {})
        if anp.get("execucao", {}).get("origem") != "anexo":
            falhas.append("o cruzamento deveria vir do anexo")
        if anp.get("execucao", {}).get("acoes") != 1:
            falhas.append("o recorte deveria conter só a ação do anexo, não toda a unidade")
        if anp.get("vigencia_fonte", "").startswith("estimado"):
            falhas.append("a vigência oficial da página da MP deveria prevalecer")
        if anp.get("execucao", {}).get("modo") != "piso":
            falhas.append("ação preexistente deveria cair no modo piso")
        if not anp.get("execucao", {}).get("rateado"):
            falhas.append("duas MPs na mesma ação deveriam gerar rateio")
        # empenhado 3,1 bi contra LOA de 2 bi → piso de 1,1 bi; a MP abriu 3,473 de 4,023 bi
        esperado = 1_100_000_000 * (3_473_000_000 / 4_023_000_000)
        if abs(anp.get("execucao", {}).get("empenhado", 0) - esperado) > 1:
            falhas.append(f"rateio mal calculado: {anp.get('execucao', {}).get('empenhado')} "
                          f"(esperado {esperado:,.0f})")
        # as parcelas das MPs concorrentes têm de somar o total executado
        outra = med.get("MPV 1372/2026", {})
        junto = anp.get("execucao", {}).get("empenhado", 0) + outra.get("execucao", {}).get("empenhado", 0)
        if abs(junto - 1_100_000_000) > 1:
            falhas.append(f"as parcelas não fecham o total: {junto:,.0f}")
        if anp.get("execucao", {}).get("pct_empenhado") != outra.get("execucao", {}).get("pct_empenhado"):
            falhas.append("a taxa de execução deveria ser a mesma para as MPs da mesma ação")
        if anp.get("vigencia_fim") != "2026-09-28":
            falhas.append(f"vigência oficial: {anp.get('vigencia_fim')}")
        if not anp.get("sem_relator"):
            falhas.append("MPV sem relator não foi sinalizada")

        ibama = med.get("MPV 1367/2026", {})
        if ibama.get("execucao", {}).get("modo") != "direta":
            falhas.append("ação nova deveria cair no modo direta")

    if falhas:
        print("FALHOU")
        for f in falhas:
            print("  -", f)
        return 1
    print(f"OK — {len(propostas)} propostas; anexo, cruzamento exato, rateio, "
          f"vigência oficial e corte entre registros conferidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
