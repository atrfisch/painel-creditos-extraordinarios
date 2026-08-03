#!/usr/bin/env Rscript
# Execução orçamentária federal via pacote orcamentoBR (API do SIOP/SOF).
#
# Uso: Rscript coleta/siop.R 2026 2025
#
# Grava dados/siop_<ano>.csv com uma linha por UO x Ação, contendo dotação
# inicial (LOA), dotação atual (LOA + créditos) e execução (empenhado,
# liquidado, pago). A consolidação em Python faz o cruzamento com as MPs.
#
# Nota importante: a API não separa crédito extraordinário de suplementar ou
# especial. O que se obtém aqui é a dotação por ação; o painel isola as ações
# abertas por crédito (LOA = 0) e permite fixar o de-para exato em
# config/de_para_acoes.csv.

args <- commandArgs(trailingOnly = TRUE)
anos <- if (length(args) > 0) as.integer(args) else as.integer(format(Sys.Date(), "%Y"))

if (!requireNamespace("orcamentoBR", quietly = TRUE)) {
  install.packages("orcamentoBR", repos = "https://cloud.r-project.org")
}
library(orcamentoBR)

dir.create("dados", showWarnings = FALSE, recursive = TRUE)

normalizar <- function(df) {
  # o pacote pode variar os nomes das colunas entre versões; padroniza aqui
  nomes <- names(df)
  achar <- function(padrao) {
    hit <- grep(padrao, nomes, ignore.case = TRUE, value = TRUE)
    if (length(hit) == 0) NA_character_ else hit[1]
  }
  mapa <- c(
    codigo_uo        = achar("^codigo.*uo$|^cod.*unidade|^uo$"),
    unidade          = achar("^(descricao)?uo$|unidade.*orcament|^uo_desc"),
    codigo_acao      = achar("^codigo.*acao$|^cod.*acao"),
    acao             = achar("^(descricao)?acao$|^acao_desc|^acao$"),
    loa              = achar("loa$|dotacao.*inicial"),
    loa_mais_credito = achar("credito"),
    empenhado        = achar("empenhad"),
    liquidado        = achar("liquidad"),
    pago             = achar("pago")
  )
  saida <- data.frame(matrix(NA, nrow = nrow(df), ncol = length(mapa)))
  names(saida) <- names(mapa)
  for (campo in names(mapa)) {
    col <- mapa[[campo]]
    if (!is.na(col)) saida[[campo]] <- df[[col]]
  }
  saida
}

for (ano in anos) {
  message("SIOP: baixando exercício ", ano, " ...")
  bruto <- tryCatch(
    despesaDetalhada(
      exercicio           = ano,
      UO                  = TRUE,
      Acao                = TRUE,
      valorPLOA           = FALSE,
      valorLOA            = TRUE,
      valorLOAmaisCredito = TRUE,
      valorEmpenhado      = TRUE,
      valorLiquidado      = TRUE,
      valorPago           = TRUE,
      incluiDescricoes    = TRUE
    ),
    error = function(e) {
      message("  falhou: ", conditionMessage(e))
      NULL
    }
  )

  if (is.null(bruto) || nrow(bruto) == 0) {
    message("  sem dados para ", ano, ", pulando")
    next
  }

  write.csv(bruto, sprintf("dados/siop_%d_bruto.csv", ano), row.names = FALSE, fileEncoding = "UTF-8")
  limpo <- normalizar(bruto)
  write.csv(limpo, sprintf("dados/siop_%d.csv", ano), row.names = FALSE, fileEncoding = "UTF-8")
  message("  ", nrow(limpo), " linhas gravadas")
}
