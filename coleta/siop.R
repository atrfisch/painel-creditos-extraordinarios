#!/usr/bin/env Rscript
# Execução orçamentária federal via pacote orcamentoBR (API do SIOP/SOF).
#
# Uso: Rscript coleta/siop.R 2026 2025
#
# Consulta apenas as unidades orçamentárias que aparecem nos anexos das MPs,
# lidas de dados/uos.txt. Puxar o exercício inteiro cruzando todas as unidades
# com todas as ações é grande demais e estoura o tempo da API.
#
# O script começa por uma sonda mínima. Se ela falhar, o problema é a API ou o
# pacote; se passar e as consultas por unidade falharem, o problema é o filtro.
# Sem essa separação não dá para saber qual das duas coisas corrigir.

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
    message("::warning::dados/uos.txt não existe — a coleta de anexos não rodou ou não achou programática")
    return(character(0))
  }
  uos <- trimws(readLines("dados/uos.txt", warn = FALSE))
  unique(uos[nzchar(uos)])
}

valores <- list(valorPLOA = FALSE, valorLOA = TRUE, valorLOAmaisCredito = TRUE,
                valorEmpenhado = TRUE, valorLiquidado = TRUE, valorPago = TRUE)

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

# --- sonda: a API responde? ------------------------------------------------
sondar <- function(ano) {
  message("  sonda: consulta agregada do exercício")
  r <- tentar("sonda", chamar(ano, url = TRUE))
  if (is.null(r) || nrow(r) == 0) {
    message("::error::a API do SIOP não respondeu nem à consulta mais simples para ", ano,
            " — o problema não é o filtro por unidade")
    return(FALSE)
  }
  message("    ok: ", nrow(r), " linha(s), colunas: ", paste(names(r), collapse = ", "))
  TRUE
}

# --- mapeamento de colunas -------------------------------------------------
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
    message("::warning::colunas não mapeadas: ", paste(faltando, collapse = ", "),
            " | recebidas: ", paste(nomes, collapse = ", "))
  }
  saida <- data.frame(matrix(NA, nrow = nrow(df), ncol = length(mapa)))
  names(saida) <- names(mapa)
  for (campo in names(mapa)) {
    col <- mapa[[campo]]
    if (!is.na(col)) saida[[campo]] <- df[[col]]
  }
  saida
}

# --- consulta por unidade --------------------------------------------------
consultar <- function(ano, uos) {
  if (!length(uos)) {
    message("  sem lista de unidades — consultando o exercício inteiro por UO")
    return(tentar("exercício inteiro", chamar(ano, UO = TRUE, Acao = TRUE)))
  }

  partes <- list()
  falhas <- character(0)

  # tenta em lote; alguns filtros da API não aceitam vetor, então cai para uma
  # unidade por vez em caso de erro
  lotes <- split(uos, ceiling(seq_along(uos) / 5))
  for (i in seq_along(lotes)) {
    lote <- lotes[[i]]
    message("  lote ", i, "/", length(lotes), ": ", paste(lote, collapse = ", "))
    parte <- tentar("lote", chamar(ano, UO = lote, Acao = TRUE))

    if (is.null(parte) || nrow(parte) == 0) {
      for (uo in lote) {
        message("    tentando UO ", uo, " isolada")
        uma <- tentar(paste("UO", uo), chamar(ano, UO = uo, Acao = TRUE,
                                              url = (length(partes) == 0)))
        if (!is.null(uma) && nrow(uma) > 0) {
          partes[[length(partes) + 1]] <- uma
        } else {
          falhas <- c(falhas, uo)
        }
        Sys.sleep(0.5)
      }
    } else {
      partes[[length(partes) + 1]] <- parte
    }
    Sys.sleep(1)
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

  if (!sondar(ano)) next

  bruto <- consultar(ano, uos)
  if (is.null(bruto) || nrow(bruto) == 0) {
    message("::warning::SIOP não retornou dados para ", ano)
    next
  }

  message("  colunas: ", paste(names(bruto), collapse = ", "))
  limpo <- normalizar(bruto)
  limpo <- limpo[!is.na(limpo$codigo_uo), , drop = FALSE]
  write.csv(limpo, sprintf("dados/siop_%d.csv", ano), row.names = FALSE, fileEncoding = "UTF-8")
  message("  ", nrow(limpo), " linhas gravadas")

  soma <- suppressWarnings(sum(as.numeric(limpo$empenhado), na.rm = TRUE))
  message("  empenho total: ", format(soma, big.mark = ".", decimal.mark = ","))
  if (!is.finite(soma) || soma == 0) {
    message("::warning::empenho somando zero — verifique o mapeamento acima")
  }
}
