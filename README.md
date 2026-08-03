# Painel de créditos extraordinários

Acompanhamento das medidas provisórias que abrem crédito extraordinário, cruzando a
tramitação no Congresso com a execução orçamentária federal. Publica-se sozinho no
GitHub Pages.

Cada medida aparece com duas réguas paralelas: em cima o prazo constitucional de
vigência, embaixo quanto do crédito já virou empenho e pagamento. É esse par que o
painel existe para mostrar — dinheiro autorizado por urgência que pode caducar antes
de sair do papel.

## O que entra no painel

| Campo | Origem |
|---|---|
| Número, ementa, valor, unidades orçamentárias | Congresso Nacional — Propostas Orçamentárias |
| Relator | idem |
| Prazo de emendas e quantidade de emendas | idem |
| Situação | idem |
| Prazo de vigência | Dados Abertos do Senado + regra do art. 62 |
| Dotação atual, empenhado, liquidado, pago | SIOP/SOF via pacote `orcamentoBR` |

## Instalação

```bash
git clone https://github.com/<usuario>/painel-creditos-extraordinarios
cd painel-creditos-extraordinarios
pip install -r requirements.txt
Rscript -e 'install.packages("orcamentoBR", repos="https://cloud.r-project.org")'
```

Em **Settings → Pages**, defina a origem como **GitHub Actions**. O workflow roda de
segunda a sexta, às 6h e 18h de Brasília, e também sob demanda em **Actions → Atualizar
painel → Run workflow**.

## Rodar localmente

```bash
python coleta/congresso.py 2026 2025   # raspa a tabela do Congresso
python coleta/vigencia.py              # data de publicação e prazo de vigência
Rscript coleta/siop.R 2026 2025        # execução orçamentária
python coleta/consolidar.py            # gera site/dados.json
python -m http.server -d site 8000
```

Rode a primeira vez com atenção ao que o `consolidar.py` imprime no final: ele lista as
unidades orçamentárias que não encontrou no SIOP.

## Os três arquivos de ajuste

Ficam em `config/` e existem porque nenhuma das duas bases foi feita para conversar com
a outra.

**`de_para_uo.csv`** — `nome_congresso,codigo_uo,observacao`. O casamento entre as duas
bases é feito pelo nome da unidade orçamentária, normalizado. A tabela do Congresso tem
grafias próprias e alguns erros de digitação (há um "Advocacia gerla da Uniao" no ar
hoje), então nomes que o normalizador não resolver aparecem no log e devem ser fixados
aqui.

**`de_para_acoes.csv`** — `identificacao,codigo_uo,codigo_acao,observacao`. A API do SIOP
não marca qual crédito abriu qual dotação: ela entrega dotação inicial e dotação atual, e
a diferença mistura extraordinário, especial e suplementar. Por padrão o painel usa como
recorte as ações da unidade com dotação inicial zero, que é o comportamento típico do
crédito extraordinário. Quando a atribuição exata importar, copie as ações do anexo da MP
para cá — havendo linha para a matéria, a heurística é ignorada.

**`vigencia_manual.csv`** — `identificacao,publicacao,vigencia_60,vigencia_fim,fonte`. A
vigência é calculada a partir da data de publicação: 60 dias, prorrogados uma vez por
igual período, com a contagem suspensa no recesso. É estimativa. Para as MPs em que a
data exata pesa, transcreva aqui o que diz o Ato do Presidente da Mesa do Congresso.

Cada ficha mostra de onde veio a sua data de vigência, em "Unidades orçamentárias e
execução".

## Testes

```bash
python tests/testar.py
```

Roda o parser contra uma cópia da estrutura real da página e valida o cruzamento com um
SIOP sintético. Útil antes de mexer nas expressões regulares de `coleta/congresso.py`,
que é a parte que quebra quando o portal do Congresso muda de layout.

## Limites conhecidos

- A execução é agregada por unidade orçamentária, não por ação individual da MP, a menos
  que o de-para de ações esteja preenchido. Em unidades que receberam mais de um crédito
  no mesmo exercício, os valores se somam.
- Créditos que apenas reforçam ação já existente na LOA não são capturados pela
  heurística de dotação inicial zero; para esses, o de-para de ações é obrigatório.
- A situação e a contagem de emendas refletem o que o portal do Congresso publica, com o
  atraso que ele tiver.
