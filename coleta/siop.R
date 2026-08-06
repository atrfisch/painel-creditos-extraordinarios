#!/usr/bin/env Rscript
# Execução orçamentária federal via pacote orcamentoBR (API do SIOP/SOF).
#
# Uso: Rscript coleta/siop.R 2026 2025
#
# Consulta apenas as unidades orçamentárias que aparecem nos anexos das MPs,
# lidas de dados/uos.txt. Puxar o exercício inteiro cruzando todas as unidades
# com todas as ações é uma consulta grande demais: ela estoura o tempo da API e,
# como a etapa do workflow tolera erro, a falha passa despercebida e o painel
# aparece sem execução nenhuma. Com os códigos vindos do anexo, cada consulta
# fica pequena.
#
# Grava dados/siop_<ano>.csv com uma linha por UO x Ação.

args <- commandArgs(trailingOnly = TRUE)
anos <- if (length(args) > 0) as.integer(args) else as.integer(format(Sys.Date(), "%Y"))

if (!requireNamespace("orcamentoBR", quietly = TRUE)) {
  install.packages("orcamentoBR", repos = "https://cloud.r-project.org")
}
library(orcamentoBR)

dir.create("dados", showWarnings = FALSE, recursive = TRUE)

ler_uos <- function() {
  if (!file.exists("dados/uos.txt")) return(character(0))
  uos <- trimws(readLines("dados/uos.txt", warn = FALSE))
  unique(uos[nzchar(uos)])
}

# --- mapeamento de colunas -------------------------------------------------
# Os nomes vêm do orcamentoBR e podem variar entre versões. Tenta o nome exato
# primeiro e só então cai para a busca por padrão, para não confundir a coluna
# de código com a de descrição (ambas contêm "UO").
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
    codigo_uo        = mapear(nomes, c("codigoUO", "cod_uo", "codigoUnidadeOrcamentaria"), "^codigo.*uo"),
    unidade          = mapear(nomes, c("UO", "unidadeOrcamentaria", "descricaoUO"), "unidade.*orcament"),
    codigo_acao      = mapear(nomes, c("codigoAcao", "cod_acao"), "^codigo.*acao"),
    acao             = mapear(nomes, c("Acao", "descricaoAcao"), "^acao$"),
    loa              = mapear(nomes, c("valorLOA"), "loa$|dotacao.*inicial"),
    loa_mais_credito = mapear(nomes, c("valorLOAmaisCredito"), "credito"),
    empenhado        = mapear(nomes, c("valorEmpenhado"), "empenhad"),
    liquidado        = mapear(nomes, c("valorLiquidado"), "liquidad"),
    pago             = mapear(nomes, c("valorPago"), "pago")
  )

  faltando <- names(mapa)[is.na(mapa)]
  if (length(faltando)) {
    message("::warning::colunas não mapeadas no retorno do SIOP: ", paste(faltando, collapse = ", "))
    message("  colunas recebidas: ", paste(nomes, collapse = ", "))
  }

  saida <- data.frame(matrix(NA, nrow = nrow(df), ncol = length(mapa)))
  names(saida) <- names(mapa)
  for (campo in names(mapa)) {
    col <- mapa[[campo]]
    if (!is.na(col)) saida[[campo]] <- df[[col]]
  }
  saida
}

consultar <- function(ano, uos) {
  chamar <- function(...) {
    despesaDetalhada(
      exercicio = ano, Acao = TRUE, incluiDescricoes = TRUE,
      valorPLOA = FALSE, valorLOA = TRUE, valorLOAmaisCredito = TRUE,
      valorEmpenhado = TRUE, valorLiquidado = TRUE, valorPago = TRUE,
      timeout = 600, ...
    )
  }

  if (!length(uos)) {
    message("  sem lista de unidades (dados/uos.txt vazio) — consultando o exercício inteiro")
    return(tryCatch(chamar(UO = TRUE),
                    error = function(e) { message("  falhou: ", conditionMessage(e)); NULL }))
  }

  # lotes pequenos: uma unidade problemática não derruba a coleta toda
  lotes <- split(uos, ceiling(seq_along(uos) / 5))
  partes <- list()
  for (i in seq_along(lotes)) {
    lote <- lotes[[i]]
    message("  lote ", i, "/", length(lotes), ": UO ", paste(lote, collapse = ", "))
    parte <- tryCatch(chamar(UO = lote),
                      error = function(e) { message("    falhou: ", conditionMessage(e)); NULL })
    if (!is.null(parte) && nrow(parte) > 0) partes[[length(partes) + 1]] <- parte
    Sys.sleep(1)
  }
  if (!length(partes)) return(NULL)
  do.call(rbind, partes)
}

for (ano in anos) {
  uos <- ler_uos()
  message("SIOP: exercício ", ano, " — ", length(uos), " unidade(s) do anexo")
  bruto <- consultar(ano, uos)

  if (is.null(bruto) || nrow(bruto) == 0) {
    message("::warning::SIOP não retornou dados para ", ano)
    next
  }

  message("  colunas: ", paste(names(bruto), collapse = ", "))
  limpo <- normalizar(bruto)
  limpo <- limpo[!is.na(limpo$codigo_uo), , drop = FALSE]
  write.csv(limpo, sprintf("dados/siop_%d.csv", ano), row.names = FALSE, fileEncoding = "UTF-8")
  message("  ", nrow(limpo), " linhas gravadas em dados/siop_", ano, ".csv")

  soma <- suppressWarnings(sum(as.numeric(limpo$empenhado), na.rm = TRUE))
  if (!is.finite(soma) || soma == 0) {
    message("::warning::empenho somando zero em ", ano, " — verifique o mapeamento de colunas acima")
  }
}
