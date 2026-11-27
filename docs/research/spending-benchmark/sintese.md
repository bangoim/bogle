# Benchmark de gastos: síntese para a #112 (27/11/2026)

Relatórios por app nesta pasta: `cfo-silvia.md`, `anotadin.md`, `pierre.md`.

## Base de cada análise

| App | O que é | Peso da evidência |
|---|---|---|
| Pierre | App brasileiro de gastos sobre a **Pluggy**, os mesmos dados do bogle | Alto: API pública, central de ajuda, 2.442 avaliações das lojas |
| Anotadin | Landing com lista de espera; protótipo viralizou em 17/11/2026 | Baixo: landing, vídeos e ~400 respostas ao post (medem interesse, não uso) |
| CFO Silvia | Produto americano (Plaid/SnapTrade) para alto patrimônio; gastos são secundários | Médio para regras e métricas; quase nada sobre Pix, fatura ou parcela |

O Pierre é a referência mais útil: ele mostra o que dá errado com os mesmos dados da Pluggy. Os usuários deixam o app quando deixam de confiar no número: dado atrasado mostrado como atual (~92 relatos), correção de categoria que não persiste (~54), fatura que não bate com o banco (~30), transferência ou investimento contado como gasto.

## Pontos em que os três convergem

1. **Frescor dos dados** (os 3): mostrar "dados até dd/mm" por conta e avisar quando o consentimento está perto de vencer ou o item está com erro. Dado atrasado faz o mês parecer "no ritmo".
2. **Não excluir a categoria "Transfers" inteira** (os 3): Pix a terceiros (aluguel, diarista) é gasto. Só a transferência entre contas próprias sai.
3. **Classificação fechada com override manual** (Pierre e Silvia): gasto, receita, transferência própria, pagamento de fatura, investimento (aplicação/resgate), abatimento, ignorada. Resgate não é receita.
4. **Natureza do grupo** (os 3, com nomes diferentes): fixo/dia a dia (Anotadin), essencial/discricionário/poupança (Silvia), despesa/receita/interno (Pierre).
5. **Ritmo do mês** (os 3): % do limite usado contra % do mês decorrido, ou acumulado até o mesmo dia dos meses anteriores.
6. **Parcela conta no mês em que é paga** (os 3 inclinam para a visão de caixa): resolve a questão aberta da #112 a favor da data da parcela, não do `purchaseDate`.
7. **Alerta deduplicado** (os 3): uma vez por grupo e por mês. No Pierre há relatos de 17 avisos do mesmo gasto.
8. **Gasto ligado à carteira** (os 3): sobra do mês, taxa de poupança, reserva em meses de custo, custo de vida alimentando `withdraw`/glide path. É o que diferencia o bogle de todos eles.

## Proposta por sub-issue

### #114 (cliente, schema, sync)
- **Deduplicar também por `providerId`.** Na renovação do consentimento sai um item novo, com ids novos, e a Pluggy não remove as duplicadas entre itens. Sem isso, o upsert pelo id duplica um ano de histórico. (Pierre; risco também levantado no Silvia.)
- Gravar por conta o último sync bem-sucedido e a data da primeira transação disponível.
- Pedir datas futuras até a última parcela no sync, senão a troca das `PENDING` apaga parcelas.
- `bogle check`: consentimento perto de vencer e item com erro.
- Plano B (fora da v0.10): importar CSV/OFX do Nubank com a mesma deduplicação, se o acesso à Pluggy acabar.

### #115 (exclusões)
- Trocar "gasto, receita ou excluída" por um **conjunto fechado de tipos**: `gasto`, `receita`, `transferencia_propria`, `pagamento_fatura`, `investimento`, `abatimento`, `ignorada`, sempre com o motivo.
- **Transferência própria pelo CPF do favorecido** (CPF do usuário em configuração), além do casamento das duas pontas, que falha com conta não conectada.
- `RESGATE_APLIC_FINANCEIRA` e aplicações vão para `investimento`, não para receita nem para gasto.
- Fatura: casar 1 fatura com N pagamentos (parcial, antecipado, antes do fechamento); pagamento de cartão não conectado fica como gasto ou é marcado à mão.
- Estorno parcial, estorno em fatura posterior e estorno de compra parcelada entram nos critérios de aceite.
- **Ignorar transação à mão**, com precedência manual > regra > automático. Era o recurso mais usado no Pierre.
- Duplicidade provável vira sugestão, nunca exclusão automática.
- Antes de implementar: contar nos dados do Nubank quantas "Transfers" vão para terceiros.

### #116 (grupos, correção, regras)
- O desenho atual (correção > regra > Pluggy) já resolve a maior queixa do Pierre.
- **Natureza no grupo**, por exemplo `fixo` / `variavel` / `interno`. Recomendo um único eixo, essencial/fixo contra variável, porque é o que o orçamento, o ritmo e a reserva usam. Decisão sua (ver abaixo).
- Regras com mais condições combinadas por E: documento do favorecido (Pix/CNPJ), descrição com tipo de casamento (contém, começa com, igual), faixa de valor, conta. No Pierre, "mercadobelo" virou Adobe por casamento frouxo.
- Prévia de quantas transações mudam antes de salvar a regra, e desfazer.
- Mostrar a % do gasto que cai em "Outros". No exemplo oficial do Silvia, 17,5% ficam sem categoria.
- "Assinaturas" não vira grupo; recorrência é um atributo (ver ideias novas).

### #117 (resumo e tendência)
- **Mês de referência:** a parcela conta pela data dela (visão de caixa). Uma compra em 10x pesa 1/10 em cada mês, e a issue precisa dizer isso.
- Mês corrente parcial: comparar com o acumulado até o mesmo dia dos meses anteriores, e não entrar na média sem aviso.
- Meses antes do início do histórico (o cartão só tem dados desde mar/2026) não podem entrar como zero na média. Isso pede um ajuste no critério "mês sem dados aparece como zero": zero só dentro da cobertura.
- Bloco "o mês em uma conta": entra, fixos, variável, sobra, taxa de poupança.
- Estabelecimentos mais frequentes, com contagem e variação.
- Comparação com o mesmo mês do ano anterior quando houver dados.

### #118 (faturas e parcelas)
- Estados da fatura: aberta, **fechada a vencer**, paga, paga parcial.
- Conciliação visível: total da Pluggy, soma das transações, diferença e pagamentos. A diferença costuma vir de encargos, IOF e saldo anterior.
- Na projeção, não somar parcelas que já estão na fatura aberta.
- **Comprometido futuro**: quanto de cada mês já está tomado por parcelas e quanto isso pesa na receita média.
- Card da fatura aberta: "fecha em N dias", vencimento, número de compras. Parcelas com rótulo "3/10".

### #119 (orçamento)
- Limite inicial sugerido pela mediana dos últimos 3 a 6 meses.
- **Alerta de ritmo** além do limiar: projeção do mês contra o limite, calculado só sobre os grupos variáveis.
- Limiares 80% e 100%; cada faixa dispara uma vez por grupo e por mês.
- Grupos fixos fora do ritmo (opcionalmente, fora do orçamento).
- Provisão anual (IPVA, IPTU, seguro, viagem) como limite anual ÷ 12. Pode ficar para depois.
- **`bogle check`** (em aberto na #112): recomendo entrar, mas só no estouro (> 100%), deduplicado por grupo e mês. O Anotadin sugere também a faixa de 80%; fica como opção.

### #120 (TUI)
- Indicador de frescor no cabeçalho do modo Gastos.
- Ação curta na transação com três opções: corrigir categoria, criar regra com prévia, ignorar. Seleção múltipla para correção em lote.
- Motivo de cada exclusão visível na lista.
- Ordem do resumo: número principal (sobra ou pode gastar), o mês em uma conta, orçamento, fatura aberta, transações. Seletor de mês em todas as telas.
- Evitar rosca e heatmap, que dependem de legenda (regra de texto da interface).

## Ideias fora do escopo atual

Esforço: P pequeno, M médio, G grande. "Fontes" diz quantos dos três relatórios propuseram a ideia.

| # | Ideia | Fontes | Esforço | Depende de |
|---|---|---|---|---|
| 1 | Sobra do mês e taxa de poupança (mensal e 12 meses) | 3 | P | #115 tipos, #117 |
| 2 | Sobra como valor padrão do `bogle suggest --amount`; aporte efetivo contra a sobra (Pix para a corretora = aporte) | 3 | M | #115 `investimento` |
| 3 | Reserva de emergência em meses de gasto essencial, contra os ativos de reserva da carteira | 3 | M | natureza do grupo |
| 4 | Custo de vida de 12 meses alimentando `withdraw`, meta de independência e glide path | 3 | M | #117 |
| 5 | Recorrências e assinaturas por regra determinística: próxima cobrança, aumento de preço, cobrança duplicada, encerradas | 3 | M | #116 |
| 6 | Resumo semanal e vencimento da fatura no `bogle check` | 2 | P | #118, #119 |
| 7 | Anomalias: estabelecimento novo, gasto único grande, grupo acima do normal, duplicidade provável | 2 | P/M | #117 |
| 8 | Saldo previsto / calendário de 90 dias (faturas, parcelas, fixos) | 2 | M/G | #118, ideia 5 |
| 9 | "Pode gastar no mês e por dia" e "cabe?" para compra à vista ou parcelada | 1 | P | #119, #118 |
| 10 | Conferência de saldo para detectar transação faltando | 1 | P/M | #114 |
| 11 | Fechamento mensal gravado (patrimônio, fluxo, poupança, aporte) | 1 | M | ideias 1 e 2 |
| 12 | Metas com dinheiro reservado (não pelo patrimônio total) | 1 | M/G | — |
| 13 | Importação CSV/OFX como plano B | 1 | M | #114 |

As ideias 1 a 4 são o que nenhum dos três faz bem: ligar gasto e carteira no mesmo banco. Todas dependem de a #115 separar `investimento` de `transferencia_propria` e de a #116 ter a natureza do grupo. Por isso essas duas mudanças devem entrar antes da implementação.

## Decisões pendentes

1. Incorporar às sub-issues as mudanças de escopo acima (principalmente #114 `providerId`, #115 tipos fechados, CPF e ignorar, #116 natureza do grupo, #117 parcela pela data dela e meses fora da cobertura).
2. Eixo da natureza do grupo: fixo/variável, essencial/discricionário, ou os dois.
3. `bogle check`: só estouro ou também 80%.
4. Ideias novas: quais viram sub-issues da v0.10 e quais vão para um épico seguinte.
