#!/usr/bin/env Rscript
# Execução orçamentária federal via pacote orcamentoBR (API do SIOP/SOF).
#
# Uso: Rscript coleta/siop.R 2026 2025
#
# Consulta uma unidade orçamentária por vez, usando os códigos que vieram dos
# anexos das MPs (dados/uos.txt). Duas restrições justificam esse formato:
#
#   - o parâmetro UO aceita um código só. Passar um vetor faz o pacote quebrar
#     com "the condition has length > 1", porque ele testa o argumento com um
#     `if` que espera valor único;
#   - puxar o exercício inteiro cruzando todas as unidades com todas as ações
#     é grande demais e estoura o tempo da API.
#
# Grava dados/siop_<ano>.csv com uma linha por UO x Ação.

args <- commandArgs(trailingOnly = TRUE)
anos <- if (length(args) > 0) as.integer(args) else as.integer(format(Sys.Date(), "%Y"))

if (!requireNamespace("orcamentoBR", quietly = TRUE)) {
  install.packages("orcamentoBR", repos = "https://cloud.r-project.org")
}
library(orcamentoBR)
message("orcamentoBR ", as.character(packageVersion("orcamentoBR")))

dir.create("dados", showWarnings = FALSE, recursive = TRUE)

ler_uos <- function() {
  if (!file.exists("dados/uos.txt")) {
    message("::warning::dados/uos.txt não existe — a coleta de anexos não rodou ",
            "ou não achou programática")
    return(character(0))
  }
  uos <- trimws(readLines("dados/uos.txt", warn = FALSE))
  unique(uos[nzchar(uos)])
}

valores <- list(valorPLOA = FALSE, valorLOA = TRUE, valorLOAmaisCredito = TRUE,
                valorEmpenhado = TRUE, valorLiquidado = TRUE, valorPago = TRUE)

# O subtítulo (localizador) é o que separa o crédito extraordinário do restante
# da ação: o crédito abre subtítulo próprio, e é ele que o anexo da MP informa.
# Sem essa dimensão, a consulta devolve a ação inteira somada e não há como
# distinguir o que é do crédito do que já estava na LOA.
dimensoes <- list(Acao = TRUE, Subtitulo = TRUE)

chamar <- function(ano, ..., url = FALSE) {
  do.call(despesaDetalhada, c(list(exercicio = ano, incluiDescricoes = TRUE,
                                   timeout = 600, print_url = url), valores, list(...)))
}

tentar <- function(rotulo, expr) {
  tryCatch(expr, error = function(e) {
    message("    ", rotulo, " falhou: ", conditionMessage(e))
    NULL
  })
}

# --- mapeamento de colunas -------------------------------------------------
# A API devolve UO_cod / UO_desc / Acao_cod / Acao_desc. Os nomes exatos vêm
# primeiro; o padrão é rede de segurança para o caso de mudarem de versão.
mapear <- function(nomes, exatos, padrao) {
  for (nome in exatos) {
    hit <- nomes[tolower(nomes) == tolower(nome)]
    if (length(hit)) return(hit[1])
  }
  hit <- grep(padrao, nomes, ignore.case = TRUE, value = TRUE)
  if (length(hit)) hit[1] else NA_character_
}

normalizar <- function(df) {
  nomes <- names(df)
  mapa <- c(
    codigo_uo        = mapear(nomes, c("UO_cod", "codigoUO", "cod_uo"), "^uo_?cod|^codigo.*uo"),
    unidade          = mapear(nomes, c("UO_desc", "UO", "descricaoUO"), "^uo_?desc|unidade.*orcament"),
    codigo_acao      = mapear(nomes, c("Acao_cod", "codigoAcao", "cod_acao"), "^acao_?cod|^codigo.*acao"),
    acao             = mapear(nomes, c("Acao_desc", "Acao", "descricaoAcao"), "^acao_?desc|^acao$"),
    codigo_subtitulo = mapear(nomes, c("Subtitulo_cod", "codigoSubtitulo", "Localizador_cod"),
                              "^subtitulo_?cod|^codigo.*subtitulo|localizador.*cod"),
    subtitulo        = mapear(nomes, c("Subtitulo_desc", "Subtitulo", "descricaoSubtitulo"),
                              "^subtitulo_?desc|^subtitulo$|^localizador"),
    loa              = mapear(nomes, c("loa", "valorLOA"), "^loa$|dotacao.*inicial"),
    loa_mais_credito = mapear(nomes, c("loa_mais_credito", "valorLOAmaisCredito"), "credito"),
    empenhado        = mapear(nomes, c("empenhado", "valorEmpenhado"), "empenhad"),
    liquidado        = mapear(nomes, c("liquidado", "valorLiquidado"), "liquidad"),
    pago             = mapear(nomes, c("pago", "valorPago"), "^pago$|valorpago")
  )

  faltando <- names(mapa)[is.na(mapa)]
  if (length(faltando)) {
    message("::error::colunas não mapeadas: ", paste(faltando, collapse = ", "),
            " | recebidas: ", paste(nomes, collapse = ", "))
  }

  saida <- data.frame(matrix(NA, nrow = nrow(df), ncol = length(mapa)))
  names(saida) <- names(mapa)
  for (campo in names(mapa)) {
    col <- mapa[[campo]]
    if (!is.na(col)) saida[[campo]] <- df[[col]]
  }

  # códigos são identificadores, não números: 5 dígitos com zero à esquerda.
  # formatC só preenche com zero em modo numérico, então converte antes.
  padrao_uo <- function(x) {
    x <- trimws(as.character(x))
    n <- suppressWarnings(as.integer(x))
    ifelse(is.na(n), x, formatC(n, width = 5, flag = "0"))
  }
  saida$codigo_uo <- padrao_uo(saida$codigo_uo)
  saida$codigo_acao <- toupper(trimws(as.character(saida$codigo_acao)))
  # o subtítulo tem 4 dígitos e também perde zeros à esquerda se virar número
  sub <- trimws(as.character(saida$codigo_subtitulo))
  n <- suppressWarnings(as.integer(sub))
  saida$codigo_subtitulo <- ifelse(is.na(n), sub, formatC(n, width = 4, flag = "0"))
  saida
}

# --- consulta --------------------------------------------------------------
consultar <- function(ano, uos) {
  if (!length(uos)) {
    message("  sem lista de unidades — consultando o exercício inteiro por UO")
    return(tentar("exercício inteiro",
                  do.call(chamar, c(list(ano, UO = TRUE), dimensoes))))
  }

  partes <- list()
  falhas <- character(0)
  for (i in seq_along(uos)) {
    uo <- uos[i]
    parte <- tentar(paste("UO", uo),
                    do.call(chamar, c(list(ano, UO = uo, url = (i == 1)), dimensoes)))
    if (i == 1 && !is.null(parte)) {
      message("  colunas devolvidas pela API: ", paste(names(parte), collapse = ", "))
    }
    if (!is.null(parte) && nrow(parte) > 0) {
      message("  UO ", uo, ": ", nrow(parte), " ações")
      partes[[length(partes) + 1]] <- parte
    } else {
      falhas <- c(falhas, uo)
    }
    Sys.sleep(0.5)
  }

  if (length(falhas)) {
    message("::warning::sem retorno para as unidades: ", paste(falhas, collapse = ", "))
  }
  if (!length(partes)) return(NULL)
  do.call(rbind, partes)
}

for (ano in anos) {
  uos <- ler_uos()
  message("SIOP: exercício ", ano, " — ", length(uos), " unidade(s) do anexo")
  if (length(uos)) message("  unidades: ", paste(uos, collapse = ", "))

  bruto <- consultar(ano, uos)
  if (is.null(bruto) || nrow(bruto) == 0) {
    message("::error::SIOP não retornou dados para ", ano)
    next
  }

  message("  colunas: ", paste(names(bruto), collapse = ", "))
  limpo <- normalizar(bruto)
  antes <- nrow(limpo)
  limpo <- limpo[!is.na(limpo$codigo_uo) & nzchar(limpo$codigo_uo) &
                   limpo$codigo_uo != "NA", , drop = FALSE]
  if (nrow(limpo) < antes) {
    message("  ", antes - nrow(limpo), " linha(s) descartadas por falta de código de UO")
  }

  write.csv(limpo, sprintf("dados/siop_%d.csv", ano), row.names = FALSE, fileEncoding = "UTF-8")
  message("  ", nrow(limpo), " linhas gravadas")

  soma <- suppressWarnings(sum(as.numeric(limpo$empenhado), na.rm = TRUE))
  message("  empenho total: ", format(soma, big.mark = ".", decimal.mark = ",", scientific = FALSE))
  vazios <- sum(is.na(limpo$codigo_subtitulo) | !nzchar(limpo$codigo_subtitulo))
  message("  subtítulo preenchido em ", nrow(limpo) - vazios, " de ", nrow(limpo), " linhas")
  if (vazios == nrow(limpo)) {
    message("::error::a API não devolveu subtítulo. Sem essa dimensão o crédito ",
            "extraordinário não se separa da LOA e os números não mudam. ",
            "Compare a lista de colunas acima com os nomes esperados em normalizar().")
  }
  if (!is.finite(soma) || soma == 0) {
    message("::error::empenho somando zero — o mapeamento de colunas acima está errado")
  }
}
