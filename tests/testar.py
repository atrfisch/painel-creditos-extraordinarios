"""Testes offline do parser, do cálculo de vigência e do cruzamento. Não usa rede."""

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "coleta"))

import congresso  # noqa: E402

SIOP = """codigo_uo,unidade,codigo_acao,codigo_subtitulo,subtitulo,acao,loa,loa_mais_credito,empenhado,liquidado,pago
32265,ANP,00YN,0001,Nacional,Subvenção à produção,1000000000,1000000000,700000000,600000000,560000000
32265,ANP,00YN,6511,Nacional (Crédito Extraordinário),Subvenção à produção,0,2000000000,900000000,700000000,650000000
32265,ANP,00YN,6509,Nacional (Crédito Extraordinário),Subvenção à produção,0,1800000000,700000000,540000000,500000000
32265,ANP,00YO,6512,Nacional (Crédito Extraordinário),Subvenção à importação,0,1473000000,600000000,480000000,440000000
32265,ANP,00YO,6510,Nacional (Crédito Extraordinário),Subvenção à importação,0,1530000000,500000000,400000000,360000000
32265,ANP,2000,0001,Nacional,Administração da unidade,480000000,480000000,310000000,290000000,280000000
44201,IBAMA,214M,6503,Nacional (Crédito Extraordinário),Prevenção e combate a incêndios,0,120000000,90000000,70000000,66000000
44201,IBAMA,214N,6504,Nacional (Crédito Extraordinário),Brigadas florestais,0,74417722,60000000,50000000,44000000
44207,ICMBio,214P,6505,Nacional (Crédito Extraordinário),Fiscalização ambiental,0,143065710,40000000,31000000,28000000
53101,MIDR,00XZ,6500,Nacional (Crédito Extraordinário),Apoio financeiro a famílias,0,150000000,150000000,140000000,135000000
53101,MIDR,22BO,0031,Zona da Mata,Ações de defesa civil,300000000,300000000,260000000,240000000,232000000
53101,MIDR,22BO,6502,Nacional (Crédito Extraordinário),Ações de defesa civil,0,116512000,110000000,100000000,96000000
33904,FRGPS,00SJ,0001,Nacional,Benefícios Previdenciários,600000000000,600000000000,400000000000,380000000000,370000000000
55901,FNAS,00H5,0001,Nacional,BPC à Pessoa Idosa,90000000000,90000000000,60000000000,55000000000,52000000000
32264,ANEEL,2000,0001,Nacional,Administração da Unidade,34000000,34000000,20000000,18000000,17000000
36212,ANVISA,20AK,0001,Nacional,Vigilância Sanitária de Produtos,84000000,84000000,50000000,44000000,42000000
36901,FNS,21DK,0001,Nacional,Compensação Previdenciária - COMPREV,0,0,0,0,0
"""


def main() -> int:
    falhas = []
    html = (RAIZ / "tests/fixture.html").read_text(encoding="utf-8")
    propostas = congresso.parsear(html)
    por_id = {p.identificacao: p for p in propostas}

    if len(propostas) != 8:
        falhas.append(f"esperava 8 propostas, achei {len(propostas)}")

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

    # prazos: a página traz as datas em duas disposições
    from anexos import _janela, RE_URGENCIA, ROTULO_DELIB, ROTULO_EMENDAS  # noqa: E402
    abertos = ("Prazos abertos 31/07/2026 - 28/09/2026: Deliberação da Medida Provisória "
               "(Art. 10 da Res. 1/2002-CN) 31/07/2026 - 10/08/2026: Apresentação de Emendas "
               "à Medida Provisória Regime de Urgência 14/09/2026 em diante")
    calendario = ("Deliberação da Medida Provisória: 21/07/2026 a 18/09/2026 "
                  "Apresentação de emendas: 21/07/2026 a 10/08/2026 "
                  "Regime de urgência, a partir de: 04/09/2026")
    if _janela(abertos, ROTULO_DELIB) != ("31/07/2026", "28/09/2026"):
        falhas.append("prazos no formato 'Prazos abertos' (datas antes do rótulo) não lidos")
    if _janela(calendario, ROTULO_DELIB) != ("21/07/2026", "18/09/2026"):
        falhas.append("prazos no formato 'Calendário' (datas depois do rótulo) não lidos")
    if _janela(abertos, ROTULO_EMENDAS) != ("31/07/2026", "10/08/2026"):
        falhas.append("janela de emendas não lida no formato 'Prazos abertos'")
    for texto, esperado in ((abertos, "14/09/2026"), (calendario, "04/09/2026")):
        m = RE_URGENCIA.search(texto)
        achado = next((g for g in m.groups() if g), None) if m else None
        if achado != esperado:
            falhas.append(f"regime de urgência: {achado} (esperado {esperado})")

    # "NA" vindo do R não pode virar chave de subtítulo válida
    from consolidar import texto  # noqa: E402
    if texto("NA") or texto("nan") or texto("  NA  "):
        falhas.append("valores NA do R não estão sendo tratados como ausência")
    if texto("6500") != "6500":
        falhas.append("texto() está descartando valor válido")

    # OCR: normalização de códigos e recusa de valores mal formados
    from anexos import _corrigir_codigos, RE_ACAO, _num  # noqa: E402
    if _corrigir_codigos(" OOED 6520 ") != " 00ED 6520 ":
        falhas.append("O/0 do OCR não normalizado no código de ação")
    if _corrigir_codigos(" Cotas do Fundo ") != " Cotas do Fundo ":
        falhas.append("a normalização de código está alterando palavras")
    bom = RE_ACAO.search("2314 00XK 6500 Ressarcimento 09 271 547.000.000 x")
    if not bom or _num(bom.group(7)) != 547_000_000:
        falhas.append("valor bem formado deixou de ser lido")
    if RE_ACAO.search("0910 00ED 6520 Integralização 28 846 985.600.0000 x"):
        falhas.append("valor com grupo de milhar mal formado foi aceito e truncado")

    # anexo de PLN: Anexo I (suplementação) e Anexo II (cancelamento) têm os
    # mesmos códigos e valores — somar os dois dobraria o crédito
    from anexos import parsear_anexo as _pa  # noqa: E402
    pln = (RAIZ / "tests/anexo_pln23.txt").read_text(encoding="utf-8")
    esperado_parte = 150_995_494_425 + 7_178_287_214
    sup = _pa(pln)
    canc = _pa(pln, secao="cancelamento")
    if round(sum(l["valor"] for l in sup)) != esperado_parte:
        falhas.append(f"suplementação do PLN somou {sum(l['valor'] for l in sup):,.0f} "
                      f"(esperado {esperado_parte:,.0f}) — o Anexo II está sendo contado junto")
    if round(sum(l["valor"] for l in canc)) != esperado_parte:
        falhas.append("Anexo II (cancelamento) não foi lido")
    if len(sup) != 2 or len(canc) != 2:
        falhas.append(f"seções do anexo de PLN: {len(sup)} sup, {len(canc)} canc")
    # anexo de MPV, que só tem APLICAÇÃO, não pode ser afetado
    mpv_linhas = _pa((RAIZ / "tests/anexo_1378.txt").read_text(encoding="utf-8"))
    if len(mpv_linhas) != 1 or mpv_linhas[0]["valor"] != 547_000_000:
        falhas.append("o corte por seção quebrou a leitura do anexo de MPV")

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

    saida = RAIZ / "docs/dados.json"
    if saida.exists():
        dados = json.loads(saida.read_text(encoding="utf-8"))
        med = {m["identificacao"]: m for m in dados["medidas"]}
        if dados["uo_sem_correspondencia"]:
            falhas.append(f"UOs não casadas: {dados['uo_sem_correspondencia']}")

        anp = med.get("MPV 1381/2026", {})
        outra = med.get("MPV 1380/2026", {})
        ex, ex2 = anp.get("execucao", {}), outra.get("execucao", {})

        if ex.get("origem") != "anexo":
            falhas.append("o cruzamento deveria vir do anexo")
        if ex.get("modo") != "exata":
            falhas.append(f"com subtítulo o modo deveria ser exata, veio {ex.get('modo')}")
        if ex.get("acoes") != 2:
            falhas.append(f"recorte deveria ter as 2 linhas do anexo, teve {ex.get('acoes')}")
        # a ação 2000 (Administração) está na mesma UO e não pode entrar
        if ex.get("base") != 3_473_000_000:
            falhas.append(f"base contaminada por linha fora do anexo: {ex.get('base')}")

        # o subtítulo separa as duas MPs na mesma ação: cada uma tem a sua linha,
        # então não há rateio nem mistura
        if ex.get("rateado") or ex2.get("rateado"):
            falhas.append("com subtítulo próprio por MP não deveria haver rateio")
        if ex.get("empenhado") != 1_500_000_000:
            falhas.append(f"execução exata da 1381: {ex.get('empenhado')}")
        if ex2.get("empenhado") != 1_200_000_000:
            falhas.append(f"execução exata da 1380: {ex2.get('empenhado')}")

        # a ação da 1339 tem linha de LOA e linha de crédito: só a do crédito conta
        midr = med.get("MPV 1339/2026", {})
        if midr.get("execucao", {}).get("empenhado") != 260_000_000:
            falhas.append(f"linha da LOA vazou para o crédito: "
                          f"{midr.get('execucao', {}).get('empenhado')}")
        if midr.get("execucao", {}).get("modo") != "exata":
            falhas.append("crédito com subtítulo próprio não deveria cair no piso")

        if anp.get("vigencia_60") != "2026-09-28" or anp.get("vigencia_fim") != "2026-11-27":
            falhas.append(f"vigência: 60d={anp.get('vigencia_60')} fim={anp.get('vigencia_fim')}")
        if anp.get("prorrogada"):
            falhas.append("MP no primeiro período não deveria constar como prorrogada")
        if not anp.get("sem_relator"):
            falhas.append("MPV sem relator não foi sinalizada")
        if anp.get("mensagem") != "MSG 612/2026" or anp.get("despacho") != "2026-08-03":
            falhas.append("metadados do quadro da página não chegaram ao painel")

        prorrogada = med.get("MPV 1372/2026", {})
        if not prorrogada.get("prorrogada"):
            falhas.append("janela na posição dos 120 dias deveria indicar prorrogação")
        if prorrogada.get("vigencia_fim") != "2026-10-26":
            falhas.append(f"vigência prorrogada: {prorrogada.get('vigencia_fim')}")
        if prorrogada.get("dias_para_prorrogacao") is not None:
            falhas.append("MP já prorrogada não tem prazo de prorrogação pendente")

        if outra.get("prorrogada"):
            falhas.append("menção a Ato da Mesa não pode marcar prorrogação sozinha")
        if outra.get("ato_prorrogacao") is not None:
            falhas.append("Ato só deve aparecer em MP efetivamente prorrogada")

        recem = med.get("MPV 1367/2026", {})
        if recem.get("vigencia_60") != "2026-08-13" or recem.get("vigencia_fim") != "2026-10-12":
            falhas.append(f"períodos: 60d={recem.get('vigencia_60')} fim={recem.get('vigencia_fim')}")
        if (recem.get("dias_para_vigencia") or 0) < 0:
            falhas.append("MP com 1º período vencendo não pode constar como caducada")

        ibama = med.get("MPV 1367/2026", {})
        if ibama.get("execucao", {}).get("modo") != "exata":
            falhas.append("ação nova com subtítulo deveria cair no modo exata")
        if ibama.get("execucao", {}).get("acoes") != 3:
            falhas.append("as 3 ações do anexo, em 2 unidades, deveriam entrar")
        if ibama.get("execucao", {}).get("rateado"):
            falhas.append("ação exclusiva de uma MP não deveria ser rateada")
        if ibama.get("execucao", {}).get("empenhado") != 190_000_000:
            falhas.append(f"execução direta: {ibama.get('execucao', {}).get('empenhado')}")

    # ── PLNs: suplementar mede acréscimo, especial identifica ação nova ──
    shutil.copy(RAIZ / "tests/anexos_plns_fixture.json", RAIZ / "config/anexos_plns.json")
    amb = dict(os.environ, PYTHONPATH=str(RAIZ / "coleta"))
    r = subprocess.run([sys.executable, "coleta/consolidar_plns.py"], cwd=RAIZ,
                       capture_output=True, text=True, env=amb)
    if r.returncode != 0:
        falhas.append(f"consolidar_plns.py: {r.stderr[-400:]}")

    saida_plns = RAIZ / "docs/dados-plns.json"
    if saida_plns.exists():
        pl = {x["identificacao"]: x for x in
              json.loads(saida_plns.read_text(encoding="utf-8"))["plns"]}

        # o painel de PLN não pode herdar nada de vigência: PLN não caduca
        for r_ in pl.values():
            for campo in ("vigencia_fim", "vigencia_60", "prorrogada", "dias_para_vigencia"):
                if r_.get(campo) is not None:
                    falhas.append(f"{r_['identificacao']}: PLN não caduca, mas tem {campo}")

        sup = pl.get("PLN 15/2026", {})
        if sup.get("especie_credito") != "suplementar":
            falhas.append(f"espécie do PLN 15: {sup.get('especie_credito')}")
        if sup.get("acoes_total") != 2 or sup.get("acoes_novas") != 0:
            falhas.append("suplementar não deveria ter ação nova")
        # crédito de 1,7 mi sobre LOA de 34 mi = +5%
        acoes = [a for u in sup.get("unidades", []) for a in u["acoes"]]
        aneel = next((a for a in acoes if a["codigo"] == "2000"), {})
        if aneel.get("dotacao_inicial") != 34_000_000:
            falhas.append(f"dotação da ação suplementada: {aneel.get('dotacao_inicial')}")
        if aneel.get("aumento_pct") != 5.0:
            falhas.append(f"percentual de aumento: {aneel.get('aumento_pct')} (esperado 5.0)")

        esp = pl.get("PLN 14/2026", {})
        if esp.get("especie_credito") != "especial":
            falhas.append(f"espécie do PLN 14: {esp.get('especie_credito')}")
        if esp.get("acoes_novas") != 1:
            falhas.append("crédito especial deveria marcar a ação como nova")
        nova = [a for u in esp.get("unidades", []) for a in u["acoes"]][0]
        if not nova.get("nova") or nova.get("aumento_pct") is not None:
            falhas.append("ação nova não tem base de comparação, logo não tem percentual")

        # o lado do cancelamento entra na ficha
        grande = pl.get("PLN 23/2026", {})
        if not grande.get("cancelamentos"):
            falhas.append("as anulações que financiam o crédito não chegaram ao painel")
        if grande.get("total_cancelamento") != esperado_parte:
            falhas.append(f"total anulado: {grande.get('total_cancelamento')}")
        corte = grande.get("cancelamentos", [{}])[0].get("acoes", [{}])[0]
        if corte.get("reducao_pct") is None:
            falhas.append("percentual de redução sobre a LOA não calculado")

        # PLN com anexo não pode mais cair para o nível da unidade
        if grande.get("origem") != "anexo":
            falhas.append("PLN com anexo deveria ser cruzado por ele")

    if falhas:
        print("FALHOU")
        for f in falhas:
            print("  -", f)
        return 1
    print(f"OK — {len(propostas)} propostas; subtítulo, cruzamento exato, prorrogação, "
          f"prazos, PLNs (suplementar x especial) e corte entre registros conferidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
