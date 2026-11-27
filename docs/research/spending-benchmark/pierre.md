# Pierre: benchmark de gestão de gastos

Análise de produto do Pierre (https://lp.pierre.finance/) para alimentar o épico de gastos do bogle (#112, sub-issues #114 a #120). Pesquisa feita em 27/11/2026 só com fontes públicas: site, central de ajuda, documentação da API, blog, lojas de apps, imprensa e trechos do Reclame Aqui. Não foi criada conta.

**Como ler as marcações**

- **[F]** fato documentado pelo próprio Pierre (site, ajuda, API, blog, loja) ou pela imprensa, com a fonte.
- **[R]** relato de usuário em avaliação de loja ou reclamação. Mostra o que acontece na prática, mas cada relato é um caso isolado.
- **[I]** inferência minha a partir das fontes. Precisa de confirmação.

As fontes estão numeradas na seção 7 e citadas como [n].

---

## Sumário

1. Visão geral do produto
2. Inventário de funcionalidades de gestão de gastos
3. Regras de negócio e casos difíceis
4. Dores e elogios dos usuários
5. Mapeamento contra as sub-issues #114 a #120
6. Ideias fora do escopo atual
7. Fontes

**Em uma frase:** o Pierre resolve bem a importação automática (Pluggy) e o "valor percebido" de achar assinaturas e cobranças duplicadas, mas perde a confiança do usuário por números errados de fatura, categorização que não aprende de forma determinística, transferências e investimentos contados como gasto ou receita, e falta de planejamento (contas futuras, orçamento visível, metas). Quase tudo que o bogle já decidiu nas sub-issues ataca exatamente essas falhas. As lacunas que valem acrescentar estão na seção 5.

---

## 1. Visão geral do produto

### 1.1 O que é

- Assistente financeiro com IA que conecta contas por Open Finance, organiza os gastos sozinho e conversa com o usuário no app, no site e no WhatsApp [F][4][6]. O slogan é "sem planilhas, sem gráficos confusos" [F][20].
- Criado pelo engenheiro Lucas Porto, que diz que o produto "nasceu de uma frustração minha com uma fatura de cartão de crédito" [F][22]. Lançado em julho de 2025 e comprado pela CloudWalk (dona da InfinitePay) em agosto de 2025 [F][20][23][24].
- A partir de setembro de 2026 (versão 4.x) virou produto bancário: cartão de crédito próprio com cashback, CDB próprio, "Pierre Computer" (agente que opera sites) e compras feitas por agente [F][1][2][24]. A parte de gastos perdeu destaque no marketing. Na loja, o nome mudou de "Controle de gastos IA" para "IA financeira pessoal" [F][2][3].

### 1.2 Público

- Pessoa física com CPF e maior de 18 anos. Conta PJ não é suportada [F][9]. Há reclamações de menores de 17 anos barrados e de usuários que só têm conta PJ [R][3][2].
- O cartão e o CDB miram renda mensal acima de R$ 12 mil, gente jovem com dinheiro espalhado em várias instituições [F][24].
- A proposta de valor é para quem não quer disciplina de planilha. Nas palavras do fundador, "ninguém quer virar analista financeiro da própria vida" [F][21][22].

### 1.3 Modelo de negócio

| Plano | Preço | Limites |
|---|---|---|
| Básico | grátis | 1 banco, 1 agente, limite de mensagens |
| Pro | R$ 39/mês ou R$ 399/ano | até 5 bancos, 5 agentes, mais mensagens, lembretes de pagamento, relatórios personalizados |
| Premium | R$ 199/mês ou R$ 1.999/ano | bancos e mensagens ilimitados, 10 agentes, alertas inteligentes avançados |

Fonte: central de ajuda, atualizada em 22/10/2026 [F][5], e compras dentro do app na App Store [F][2]. Os agentes padrão (Albert, Marie, Galileu, Leo) não contam no limite [F][5].

- Receita também vem do cartão (1% de cashback, ou 3% para quem aplica R$ 20 mil no CDB, que vira o limite do cartão) e do CDB (111,11% e 106% do CDI) [F][1][3]. O CEO descartou crédito pessoal [F][24].
- A CloudWalk não divulga quantos pagam. O fundador fala em receita "na casa das dezenas de milhões de reais" [F][24].
- O limite de 1 banco no plano grátis é a reclamação comercial mais comum (seção 4).

### 1.4 Plataformas

- App iOS e Android, web em pierre.finance, WhatsApp e API REST + servidor MCP para levar os dados a Claude, Cursor e outros [F][4][6][7].
- O WhatsApp é canal de conversa e de avisos, inclusive para trocar categoria com botão "Confirmar" [F][6]. Não interessa ao bogle como interface, mas o padrão de confirmação aparece na seção 2.4.

### 1.5 Agregador de dados

- **Pluggy.** A ajuda diz que a Pluggy "é a empresa que conecta seus bancos via Open Finance" e é o único parceiro de dados nomeado [F][10].
- A API do Pierre confirma isso pelo formato: `creditData` (brand, level, creditLimit, balanceDueDate, balanceCloseDate, availableCreditLimit), `bankData` (transferNumber, closingBalance, automaticallyInvestedBalance), faturas com `totalAmountCurrencyCode`, `minimumPaymentAmount` e `allowsInstallments`, e status de item `UPDATED`, `UPDATING`, `LOGIN_ERROR`, `OUTDATED`, `WAITING_USER_INPUT`, `MERGING` e `PARTIAL_SUCCESS` [F][7][7c][7g]. Um usuário também cita "a comunicação da API pluggy com a Sofisa" [R][2].
- Consequência para o bogle [I]: o Pierre trabalha sobre os mesmos dados que o bogle vai receber da Meu Pluggy. Boa parte das falhas relatadas não é do Open Finance. Vem das regras que o Pierre aplica (ou deixa de aplicar) em cima desses dados.

### 1.6 Escala (números conflitantes)

| Dado | Valor | Fonte |
|---|---|---|
| Usuários | 165 mil (mar/2026) e "mais de 1,5 milhão" (ago/2026) | [F][23] e [F][24] |
| Transações processadas | 64 milhões (mar/2026), 70 milhões (set/2026) | [F][20][23] |
| Agentes criados por usuários | 190 mil (ago/2026, só MG) e 280 mil (ago/2026) | [F][25][24] |
| Google Play | nota 4,8 com 21,8 mil avaliações, 1 mi+ downloads | [F][3], consulta de 27/11/2026 |
| App Store BR | nota 4,8 com cerca de 11 mil avaliações, #43 em Finanças | [F][2] |

### 1.7 Filosofia de interface

- A empresa vende o produto como o oposto dos apps "cheios de gráficos, categorias e dashboards" [F][20]: os agentes avisam, e o usuário pergunta no chat.
- Na prática existe uma aba de painel ("Vision") com widgets, e vários usuários preferem ela ao chat. Um deles diz que "a aba vision é mais útil que a chat, devia ser a home" [R][3]. Outros pedem gráficos, relatórios e uma aba de orçamento justamente porque o chat não basta (seção 4).

---

## 2. Inventário de funcionalidades de gestão de gastos

### 2.1 Conexão e sincronização

| Item | Como funciona | Fonte |
|---|---|---|
| Conexão | Open Finance, somente leitura, autorizada no app do banco. Só PF. | [F][4][9] |
| Frequência | Atualização automática 1x/dia por banco. Sincronização manual até 3x/dia, depois "Limite diário atingido". | [F][9] |
| Reconexão | Não há botão de reconectar. É preciso remover o banco, o que apaga os dados dele de forma irreversível, e conectar de novo. | [F][9] |
| Ocultar conta | Tira a conta da visão e mantém a conexão. Diferente de remover. | [F][9] |
| Status do sync | A API separa os itens em completos, em andamento, precisando de ação do usuário, erro de login, desatualizados, sucesso parcial e falha. | [F][7g] |
| Aviso de atraso | A ajuda admite que dados recentes podem chegar atrasados e manda sincronizar e esperar. | [F][9][8] |

### 2.2 Painel ("Vision")

Não há documentação oficial do painel. O que segue vem de avaliações e de uma página de terceiros, então tudo é [R] ou [I]:

- Saldos, gastos do mês por categoria e gráfico de bolhas com o percentual de cada categoria [R][3][31].
- Comparação receita x despesa e comparativo com o mês anterior. Este sumiu num redesenho de jun-jul/2026, e houve reclamação [R][2][3][31].
- Uma ferramenta de "ritmo de gastos", elogiada: "A ferramenta do ritmo de gastos e a IA são muito legais" [R][3, 12/05/2026].
- Abas de Categorias, Transferências, Parcelamentos, Assinaturas e Cartões [R][2][3].
- Widgets que dá para adicionar e remover, mas não reordenar [R][3, 13/08/2026].
- Versão web do painel ("Vision Web"), que pelo menos um usuário diz ter sido desativada [R][2, 23/09/2026].

### 2.3 Categorização automática

- A API diz que a categorização automática é "sempre aplicada" [F][7a]. Os exemplos usam categorias em português, como Alimentação, Transporte, Contas, Receitas e "Pagamento de Fatura" [F][7a].
- A ajuda não explica como categoriza. Pela origem dos dados, deve partir da categoria da Pluggy [I].
- Existem subcategorias, mas o usuário não cria novas e elas aparecem misturadas de jeitos diferentes na web e no celular [R][3]. O usuário cria categorias próprias, mas não consegue renomear, apagar, mudar o ícone nem dizer se a categoria é de receita ou de despesa. Há relatos de categoria criada que some [R][3].
- A loja destaca a categorização como principal valor ("categorização afiada", "a escolha automática de categoria feita pelo app já é boa") [R][2][3], mas ela também aparece entre as maiores reclamações (seção 4).

### 2.4 Correção de categoria

| Mecanismo | Como funciona | Fonte |
|---|---|---|
| Pelo chat (app, site ou WhatsApp) | O usuário pede a troca. O Pierre mostra **quantas transações vão mudar** e a nova categoria antes de aplicar. No WhatsApp, só o botão "Confirmar" vale, e por até 1 minuto. Não dá para desfazer pelo WhatsApp. | [F][11] |
| Alcance | Vale só para as transações da lista mostrada. **"Compras futuras no mesmo lugar não mudam automaticamente."** | [F][11] |
| Agente Sherlock | "Corrige as categorias dos gastos automaticamente e aprende com suas escolhas". Precisa ser ativado e ocupa vaga de agente. | [F][12] |
| Edição na tela | Existiu edição direta na transação e no resumo mensal. Foi removida do resumo na versão 3.0.2 e travou com tela preta na 3.2.x. | [R][2][3] |
| Edição em massa por prompt | Elogiada ("atualizar transações em lote por prompt") e criticada: numa troca de "todas as contas de valor 950,00", a IA mudou cerca de 10 em vez de 4 e não havia como desfazer. | [R][3] |

Resumo [I]: o Pierre não tem regra determinística por estabelecimento ou favorecido. O aprendizado depende de um agente de IA opcional, e os usuários relatam que ele não guarda. Um diz que "a memória não salva por favorecido" e outro fala do "trabalho de ter que configurar repetidas vezes qual saída iria em qual categoria" [R][2][3].

### 2.5 Ocultar transações

- Era o atalho que os usuários usavam para tirar duplicadas e lançamentos que distorcem os números, como Pix pago com cartão via PicPay. No plano grátis, ocultar tinha cota [R][3, 24/05/2026].
- Sumiu no redesenho de jun-jul/2026, o que gerou reclamações: "o ícone ocultar transações simplesmente desapareceu", "não consigo ocultar transações que estão duplicadas" [R][2][3].

### 2.6 Separar compras, pagamentos de fatura, transferências e recebimentos

O endpoint `get-transactions` com `format=structured` agrupa por conta e por tipo [F][7a]:

- No cartão, separa `purchases` de `payments` ("Separação inteligente entre pagamentos e compras").
- Por conta, separa `bank_transfer` de `received`.
- `summary.total_spent` = **"compras + transferências, excluindo pagamentos de fatura"**. Também traz `total_received`, `total_bank_transfer`, as 10 maiores categorias (`by_category`) e as 10 maiores transações.
- O exemplo do MCP confirma a soma: `total_spent` 625,50 = compras 125,50 + transferências 500 [F][7d].

Resumo [I]: a regra documentada tira o pagamento de fatura do total, mas **soma toda transferência bancária ao gasto**, sem distinguir transferência para conta própria. Isso bate com as reclamações de transferências contadas como gasto (seção 3.2).

### 2.7 Categorias mais caras

`get-expensive-categories` devolve as 3 categorias com mais gasto no período (padrão: últimos 7 dias), o total do período e o número de categorias com gasto. O texto já vem formatado "para WhatsApp" [F][7b].

### 2.8 Faturas de cartão

| Item | Como funciona | Fonte |
|---|---|---|
| Faturas fechadas | `get-bills` devolve **só faturas fechadas com vencimento já passado** (`onlyPastDue: true`), da mais recente para a mais antiga. | [F][7c][7d] |
| Fatura aberta | `get-bill-summary` traz limite, limite disponível, período e um **valor aproximado** igual à "soma de transações DEBIT com status PENDING entre a última e a próxima data de fechamento". Sempre avisa que é aproximado. | [F][7c] |
| Data de fechamento | O usuário cadastra o dia de fechamento por cartão (1–31), com flag ativo e notas (`manage-closing-date`). | [F][7c] |
| Regra de fallback | Sem fechamento cadastrado, usa **7 dias antes do próximo vencimento**. Com vários cartões e nenhum fechamento cadastrado, não calcula e recomenda cadastrar. | [F][7c] |
| Ajuste pela ajuda | "Se o valor da fatura aberta não bater com o do banco, o Pierre pode estar usando outro dia de fechamento". O usuário corrige pelo chat. | [F][8] |
| Mês da compra | O blog diz que o Pierre mostra a compra do cartão "no mês da fatura, não só no dia em que passou o cartão". | [F][15] |
| Lembrete | Assistente que avisa 3 dias antes do vencimento de qualquer fatura (Pro/Premium). | [F][16][12b] |

Inferência [I]: pelos filtros documentados, a API não tem estado para "fechada, ainda não vencida". Entre o fechamento e o vencimento, a fatura a pagar não está em `get-bills` (só pega vencidas) nem em `get-bill-summary` (que já olha o ciclo novo). Um usuário do plano grátis reclama do sintoma: "não puxa a fatura atual, somente a fatura fechada e vencida" [R][2].

### 2.9 Parcelas

- `get-installments` devolve as compras parceladas feitas no período, cada uma com **todas as parcelas** (passadas, atual e futuras): descrição, valor, número da parcela, total de parcelas, vencimento, categoria e status (POSTED/PENDING). Inclui um resumo: valor total, número de parcelas, número de compras e distribuição por quantidade de parcelas. Por padrão, o período vai do primeiro dia de 3 meses atrás ao fim do mês atual [F][7e].
- O app tem uma aba de parcelamentos. Ela mostrava a lista de parcelas pagas e a pagar e passou a mostrar só uma barra de progresso em jun/2026, o que gerou reclamação [R][3, 19/06/2026].

### 2.10 Assinaturas, recorrências e cobranças duplicadas

- A loja promete apontar "parcelas, assinaturas, cobranças duplicadas e mudanças nos gastos" "antes que vire surpresa" [F][2][3].
- Há uma aba de Assinaturas com itens marcados pelo app [R][2, 23/09/2026].
- Agente **Albert** (padrão): roda de segunda a sábado e detecta cobranças suspeitas e duplicadas [F][12]. Agente **Inspetor Bugiganga**: acha cobranças duplicadas no extrato [F][12][17].
- O caso de vitrine do fundador: o Pierre "encontrou R$ 380 em assinaturas que ela nem lembrava que existiam" [F][21].
- **Não há evidência** de que o Pierre detecte aumento de preço de assinatura, avise antes da cobrança ou tire da lista as assinaturas canceladas. O blog de assinaturas não diz isso [F][17], e um usuário diz que o app fica "desatualizado no quesito contas e assinaturas já quitadas/canceladas" [R][2].

### 2.11 Limites de gasto e alertas

| Item | Como funciona | Fonte |
|---|---|---|
| Limite por categoria | `category`, `limitAmount` (BRL) e `period` (`daily`, `weekly`, `biweekly`, `monthly`). | [F][7f] |
| Avisos | Em 50%, 75% e 100%, uma vez cada por período. No modo "recorrente", só em 100%, a cada vez que atinge. | [F][7f][7d] |
| Limite criado já estourado | Se o gasto do período já passou do limite proposto, devolve um aviso e sugere começar no próximo período (`startNextPeriod`, padrão true). | [F][7f] |
| Status | `currentSpending`, `remainingAmount`, `percentageUsed`, `status` (`ok`, `warning`, `danger`), próximo aviso e histórico dos avisos enviados. | [F][7f] |
| Cota por plano | A documentação se contradiz: "Basic 0 / Pro 2 / Premium 5" num lugar, "Plano FREE: 3/3" e "Plano PRO: 2/10" nos exemplos. As faixas de status também divergem (50–74% e 75%+ numa página, 50–99% e 100%+ em outra). | [F][7f] |
| Megamen | Agente sugerido que avisa quando o gasto **diário** passa de R$ 100. | [F][12] |
| Julius | Avisa quando um gasto "parece absurdo". | [F][12] |
| Canais | WhatsApp, e-mail e push, configurados por agente. WhatsApp só nos planos pagos. | [F][6][13] |

### 2.12 Resumos periódicos

- Ajuda (nov/2026): **Marie** resume o mês e compara com o anterior, e **Galileu** manda um resumo semanal curto [F][12].
- Imprensa (mar/2026): Albert diário, Marie quinzenal (tendências), Galileu mensal (projeções e "rebalanceamento") [F][20][23]. Os papéis mudaram com o tempo.
- Custom agent típico: "resumo semanal toda segunda de manhã" [F][12b].

### 2.13 Lembretes de pagamento

`create-payment-reminder`: título, valor, vencimento, hora do aviso, único ou recorrente (`daily`, `weekly`, `monthly`, `yearly`), status `active`, `completed` ou `inactive`, filtros de próximos e atrasados, avisos por WhatsApp e e-mail, com cota por plano [F][7h]. Relato: criar um lembrete de conta a pagar consumia um agente inteiro do plano [R][3, 28/04/2026].

### 2.14 Agentes personalizados

São criados em linguagem natural, e o Pierre escolhe condição, frequência e canal. Os exemplos oficiais ligados a gasto são: limite diário, resumo semanal, lembrete 3 dias antes de qualquer fatura e acompanhamento de meta de poupança (R$ 5 mil até o fim do ano). Cada agente tem "Executar agora" e histórico de execuções [F][12b][13].

### 2.15 Lançamentos manuais

- Não existe tela de lançamento manual. Dá para "inserir novas informações de gastos em conversas com a IA e unir eles aos gastos do banco" [R][3, 20/07/2026].
- Os relatos dizem que isso duplica quando o banco manda a mesma transação ("quando você coloca manual, pode acabar gerando duplicidade") e que a IA confirma lançamentos que não grava [R][2][3].

### 2.16 Metas, orçamento como tela e planejamento

- **Não há** módulo de metas nem tela de orçamento. Os únicos caminhos são agentes personalizados (meta de poupança, agente "Alan Turing" que ajuda a juntar R$ 6 mil para um setup) [F][12][12b].
- É uma das lacunas mais pedidas (seção 4).

### 2.17 Investimentos e patrimônio

- Mostra os investimentos consolidados via Open Finance [F][4]. O Premium promete "visão completa do patrimônio" [F][1].
- Os agentes Leo e Tio Patinhas analisam investimentos e "sugerem oportunidades" no CDB do próprio Pierre [F][12]. A ajuda diz que os agentes não recomendam compra ou venda [F][12].
- O limite do cartão é o valor aplicado no CDB [F][1].
- Relatos: investimentos faltando ou errados, aba de investimentos "inativada e sem previsão" em jun/2026, e propaganda insistente do CDB [R][2][3].
- **Não há evidência** de ligação entre gasto e aporte (taxa de poupança, sobra do mês, reserva medida em meses de gasto).

### 2.18 Exportação

Não há exportação em CSV ou Excel. Usuários pedem [R][3].

---

## 3. Regras de negócio e casos difíceis

Cada caso traz o que o Pierre documenta, o que os usuários relatam e a lição para o bogle.

### 3.1 Pagamento de fatura (gasto contado em dobro)

- **Documentado:** o total gasto exclui pagamento de fatura [F][7a]. O blog manda tirar "o pagamento da fatura do cartão (as compras já foram contadas)" antes de somar [F][14].
- **Relatos:**
  - Fatura paga que continua aparecendo como aberta: "você efetua o pagamento e ele acusa que não pagou" [R][2].
  - "paguei fatura ante ontem e pierre fala hj que não foi ainda" [R][2].
  - "O app não considera pagamentos antes da fatura fechar e nisso a conta fica errada" [R][2, 24/07/2026].
  - "não consegue conciliar um pagamento de fatura e deixa as fatura de cartão aberto e não deixa a gente arrumar manualmente" [R][3].
- **Lição:** o casamento precisa aceitar N pagamentos para 1 fatura (parcial, antecipado, antes do fechamento) e ter correção manual. Igualdade de valor não basta.

### 3.2 Transferências entre contas próprias

- **Documentado:**
  - O blog ensina que dinheiro entre contas próprias não é gasto e que pagar outra pessoa sem acordo de devolução é [F][14].
  - Mas o `total_spent` da API soma todas as transferências bancárias [F][7a].
- **Relatos:**
  - "a própria IA diz que em caso de mesma titularidade as mesmas não são consideradas, sendo obrigatória a configuração da conta de origem (que torna o aplicativo pago)" [R][3, 21/09/2026]. Ou seja, a transferência própria só é reconhecida se as duas pontas estiverem conectadas, e o plano grátis só conecta 1 banco.
  - "considerou as transferências que fiz como gastos" [R][3].
  - "transferências entre contas não deveria contabilizar como entradas" [R][3].
  - Itaú: "identifica a saída mas não a entrada do valor e fica apresentando como se minhas contas estivessem no negativo" [R][3].
  - A categoria Transferências "deveria separar entradas e saídas" [R][2].
- **Lição:** detectar transferência própria pelo CPF do favorecido, e não só pelo casamento das duas pontas. Uma conta própria não conectada (corretora, outro banco) é exatamente o caso em que o casamento falha.

### 3.3 Pix

- **Relatos:**
  - "muitos pix mesmo identificados com nome e entidade estão entrando como serviços" [R][3].
  - "precisa normal cada Pix que vc fizer pra poder colocar em uma categoria" [R][3].
  - "não sabe diferenciar fatura Pix com entrada de dinheiro" [R][3].
  - Pix noturno registrado com a data do dia seguinte [R][3].
  - Ponto positivo: "bom que estou podendo checar a pessoa que recebe no título" [R][3].
- **Pix pago com cartão via carteira (PicPay):** "duplica o pix e o pagamento com valor da conta e depois o valor descontado com taxa do PicPay repete". O usuário contornava ocultando transações, até o recurso sumir [R][2].
- **Falso positivo de duplicidade:** dois Pix legítimos para o mesmo estabelecimento no mesmo instante foram avisados repetidamente como duplicados (reclamação resolvida no Reclame Aqui, trecho de busca) [R][26].
- **Lição:** regra por documento do favorecido (CPF/CNPJ do `paymentData`), não só por CNPJ de merchant. Duplicidade deve ser sugestão, nunca exclusão automática.

### 3.4 Estornos

- **Documentado (blog):** "Estorno abate a categoria, não é receita". Exemplo: R$ 120 de estorno sobre R$ 310 de roupas deixa compras em R$ 190 [F][14].
- **Relatos:** "compras que foram canceladas e estornada que o sistema não percebia" e "um registro de pagamento que havia sido estornado na minha fatura, mas o Pierre não detectou esse estorno" [R][2].
- **Lição:** a regra da #115 (estorno abate a categoria original) é a mesma do blog do Pierre. O produto do Pierre é que não a aplica de forma confiável.

### 3.5 Reembolso de terceiros e despesas divididas

- **Documentado (blog):** se outra pessoa paga metade do aluguel de R$ 1.400, moradia fica em R$ 700. Contar o reembolso como salário inflaria a receita [F][14].
- **Relatos:**
  - "divido gastos com outra pessoa e não consigo categorizar isso no app" [R][3].
  - Orçamento doméstico dividido entre três, que o Pierre "não consegue lidar" [R][3].
  - Pedido de opção para casais ou grupos [R][3].
- **Lição:** permitir classificar um crédito num grupo de despesa, para que ele abata o grupo como faz o estorno.

### 3.6 Parcelas

- **Relatos:**
  - "mistura parcelamentos encerrados há meses com parcelamentos ativos" [R][3].
  - "parcelas antecipadas continuam aparecendo em lançamentos futuros" [R][3, 01/07/2026].
  - "lançamentos com parcela 01 em abril mostrava parcelas de maio, junho como pagas" [R][3].
  - "Alguns parcelamentos aparecem nas Transferências, outros não" [R][3].
  - "o app tá fazer projeções duplicando lançamentos já existentes na fatura aberta" [R][3, 17/10/2026].
  - A IA inventou parcelas "que durariam 83 anos" [R][3].
  - A barra de progresso substituiu a lista de parcelas pagas e a pagar [R][3].
- **Ponto positivo:** "a gestão de parcelas a vencer, e top ajuda bem na análise do fluxo d caixa" [R][3].
- **Lição:**
  - Projeção não pode somar parcela futura que já entrou na fatura aberta.
  - Antecipação precisa ser tratada.
  - Mostrar a lista mês a mês e não só um percentual.

### 3.7 Fatura aberta, fechamento e mês de referência

- **Documentado:**
  - Fatura aberta aproximada, fechamento cadastrado pelo usuário, com fallback de 7 dias antes do vencimento [F][7c].
  - Compra mostrada "no mês da fatura" [F][15].
  - No blog do Pierre, o Nubank fecha sempre 7 dias antes do vencimento, e o Itaú de 8 a 10 dias, variando por mês [F][16].
- **Relatos:**
  - "quase nunca consegue puxar automaticamente as datas de fechamento e vencimento, aí pede pra gente atualizar esse dado manualmente. Mesmo atualizando manual ele ainda se perde" [R][3].
  - "o dia da fatura oscila, nunca é um dia determinado" [R][3].
  - "misturou fatura do mês anterior com o atual" [R][2].
  - "ignorou compras feita no mês anterior como se fosse a fatura é somente o que está no mês corrente" [R][2].
  - "soma gastos do mês passado que já foram pagos" [R][2].
- **Mês do usuário:**
  - "não ser possível customizar o período que o App considera sendo o mês, porque caso você receba entradas nos últimos dias do mês e os seus cartões fechem no início, fica descasado os gastos" [R][3].
  - "recebo último dia do mês. Então meu mês começa com todas receita zerada e despesas no máximo... sugiro... criar uma competência" [R][3].
- **Lição:** sempre deixar explícito qual visão está na tela (data da compra ou fatura). Usar o `billClosingDate` e o `billId` da Pluggy em vez de deduzir o fechamento.

### 3.8 Duplicidades vindas do Open Finance

- **Relatos:** transações duplicadas no cartão XP, no C6 e no Inter [R][2][3]. "Muitas duplicações de despesas" [R][3]. Saldo aparecendo em dobro [R][2][3]. Ocultar era o remédio até ser removido (seção 2.5).
- **Documentado na Pluggy, não no Pierre:** uma conexão nova gera ids novos, e a Pluggy não remove duplicadas entre itens. A recomendação é reconciliar por `providerId` [F][30].
- **Lição para o bogle:** a discovery da #112 já registrou que o itemId pode mudar na renovação do consentimento anual. Ver a seção 5.1.

### 3.9 Investimentos, "guardado" e resgates automáticos

- **Relatos:**
  - "O app confunde resgate automático de conta investimento para cobrir débitos em conta corrente com entrada" [R][2, 25/08/2026].
  - "compra de NTNB sai como uma compra qualquer ao invés de investimento" [R][3].
  - "não diferencia investimentos de despesas" [R][3].
  - "não identifica corretamente os valores guardados" [R][3].
  - Ponto positivo: "se você pedir pro agente de IA arrumar o dinheiro guardado categorizado como gasto ele fica bem preciso" [R][3].
- **Lição:** aplicação e resgate (inclusive automáticos) são movimentação interna. Não são gasto nem receita. No bogle, a discovery viu `operationType=RESGATE_APLIC_FINANCEIRA` na conta do Nubank.

### 3.10 Receitas

- **Relatos:**
  - "colocou meu salário como dívida e mesmo classificando como renda ele segue como dívida" [R][3].
  - "lança recebimentos como despesas" [R][3].
  - "a parte de categoria mostra tanto receitas quanto despesas, sem separar" [R][2].
  - "não dá pra separar se a categoria é uma despesa ou uma receita" [R][3].
- **Lição:** o grupo precisa de um tipo (despesa, receita, movimento interno). A receita nunca entra no total de gasto, como a #117 já prevê.

### 3.11 Atraso e frescor dos dados

- **Relatos:**
  - Atrasos de dias, às vezes uma semana [R][2][3].
  - "Agora tá sincronizando o saldo mas não as transações, isso acontece todo final de semana principalmente no Itaú" [R][3].
  - "O Pierre não sinalizou claramente quando os dados estavam defasados — apresentou valores incorretos como se fossem precisos" [R][2, 23/05/2026].
  - O mesmo relato diz que o assistente respondia "sincronizando" sem avisar que o limite diário tinha estourado [R][2].
- **Lição:** mostrar a data do último dado em toda análise e dizer quando o sync falhou ou não andou.

### 3.12 Ruído e erro em alertas

- **Relatos:**
  - "a notificação via WhatsApp que é bugada e me enviou o msm gasto 17 vezes de 6 dias atrás" [R][3].
  - "fica me notificando sobre uma transferência que eu faço todos os meses, mesmo depois de ter ajustado o assistente. Já foram uns 5 avisos em 12h horas" [R][3].
  - "linguagem de alerta que causa ansiedade sem estar ligada à realidade" com dados errados [R][3].
- **Lição:** alerta com estado (avisa uma vez por limiar cruzado) e só sobre dados confiáveis.

### 3.13 Assinaturas

- **Relatos:** a aba de assinaturas mostra 2 de 4 itens marcados ("ficam sumindo e reaparecendo") [R][2], e itens cancelados continuam listados [R][2].
- **Ponto positivo:** "fiz uma lista das assinaturas e pagamentos recorrentes que tenho e... parei com gastação de serviços à toa" [R][2]. "gracias a ella pude ver que el tim me estaba cobrando doble" (avaliação em espanhol) [R][2].

### 3.14 O que o Open Finance não cobre

Relatos pedem conta PJ, cartões de vale-refeição e alimentação, dinheiro em espécie, bancos sem Open Finance e cartão de outra pessoa dividido [R][2][3]. Algumas instituições não conectam ou não trazem fatura: Bradesco, C6, Santander Corretora, BMG, Sofisa, Porto e InfinitePay em processamento [R][2][3].

---

## 4. Dores e elogios dos usuários

### 4.1 Método

- **Amostra:** 2.093 avaliações do Google Play (todas as que o endpoint público devolveu, de 23/09/2025 a 26/11/2026) e 349 da App Store Brasil (feed RSS público, que limita a cerca de 500), num total de **2.442 avaliações com texto**.
  - 1–2 estrelas: 814.
  - 3 estrelas: 180.
  - 4–5 estrelas: 1.448.
- **Viés:** a nota das lojas (4,8) vem em grande parte de avaliações sem texto ou curtíssimas. 980 das 1.448 avaliações positivas têm menos de 60 caracteres ("Top", "Muito bom"). As avaliações com texto pesam mais para o lado negativo do que a nota sugere.
- **Contagem:** por palavra-chave com revisão manual de amostras. Os temas se sobrepõem e os números são aproximados (ordem de grandeza, não medida exata).
- **Reclame Aqui:** a página bloqueia leitura automática (HTTP 403). Os dados vêm de trechos de busca [R][26][27].

### 4.2 Reclamações

| Tema | Avaliações (aprox.) | Exemplos |
|---|---|---|
| App não abre, login, cadastro, código, travamento | ~187 das 814 negativas | tela preta após login, código do WhatsApp não chega, trava ao editar categoria |
| Preço e plano grátis com 1 banco | ~147 (95 negativas) | "Você só pode conectar um banco... É no mínimo contraditório", "R$ 400,00 reais por ano pra visualizar uma fatura bizarramente errada do cartão de crédito" |
| Sincronização atrasada ou dados desatualizados | ~92 | atraso de dias, saldo congelado, fatura paga que aparece aberta |
| Conexão com banco falha | ~71 das negativas | Nubank em loop de autorização, C6, Santander, Bradesco |
| Pedidos de planejamento: contas futuras, previsão, contas fixas, lançamento manual | ~79 (33 negativas, 28 positivas) | "não ter como lançar despesas futuras", "projeção de saldos futuros", "contas fixas replicadas nos meses" |
| Categorização errada ou correção que não pega | ~54 | "nem criar regras para que essas classificações se mantenham", "mercadobelo" virou Adobe |
| IA erra, alucina ou inventa números | ~50 das negativas | "inventou débitos e parcelamentos", "falou aluguel: R$250, um valor nada a ver" |
| Pedido de dados pessoais (CPF, telefone) no cadastro | ~49 | pedem cadastro progressivo |
| Valor da fatura errado | ~30 | "30% menor que a realidade", "4X maior", "diferença de R$2.000,00" |
| Parcelas: erros ou perda de detalhe | ~21 | encerradas misturadas, antecipadas aparecendo, só barra de progresso |
| Criar, editar e subcategorizar categorias | ~19 | "não permite subcategorias", "não dá pra separar se a categoria é uma despesa ou uma receita" |
| Duplicidade de transações | ~18 | XP, C6, Pix via PicPay, manual + banco |
| Metas e reserva ausentes | ~18 | "não tem METAS", "criar um objetivo para ter X valor de reserva de Emergência" |
| Conta PJ | ~16 | — |
| Orçamento sem tela própria | ~12 | "não ter uma aba de orçamento é MUITO ruim", "acho péssimo ter que digitar pra IA" |
| Investimento ou "guardado" tratado como gasto ou receita | ~11 | resgate automático como entrada, NTN-B como compra |
| Transferências mal tratadas | ~9 | própria contada como gasto ou entrada |
| Mês de referência e período | ~8 | mistura de meses, pedido de competência |
| Receita classificada errado | ~8 | salário como dívida |
| Alertas repetidos ou errados | ~7 | 17 vezes o mesmo gasto |
| Estorno não detectado | ~2 explícitos | — |

**Reclame Aqui (trechos de busca):**

- Perfil "Pierre Finance" com 82 reclamações. As principais categorias são Cobrança indevida (27), Cancelamento (8), Baixa qualidade (8), Login/senha (5) e Cobrança duplicada (2) [R][26].
- Outro levantamento, de 24/09/2026, cita nota 8,6, 61 reclamações e 98,4% respondidas [F][28].
- Uma reclamação relata faturas com valores errados, parcelas mal interpretadas, transferências mal categorizadas e falsos alertas de duplicidade [R][26][29].
- No Reclame Aqui o grosso é comercial (assinatura anual cobrada no teste grátis, dificuldade de cancelar). Não interessa ao bogle.

**Linha do tempo:** as reclamações sobem com os redesenhos. O 3.0 (jun/2026) tirou edição de categoria do resumo, ocultar transações, a lista de parcelas e o comparativo mensal. O 4.0 (set/2026) virou o app para cartão e CDB. Há várias avaliações do tipo "Ao invés de corrigir bugs... preferem mudar o design" [R][3].

### 4.3 Elogios

| Tema | Avaliações positivas (aprox.) | Exemplos |
|---|---|---|
| Interface e design | ~53 | "o Pierre é uma aula de UI", "interface crocante de bela" |
| Achar assinaturas, duplicadas e gastos à toa | ~38 | "me lembrar de erros como contas duplicadas", "el tim me estaba cobrando doble" |
| Visão de faturas e cartões juntos | ~38 (inclui o cartão próprio) | "visualizar as faturas de crédito em conjunto" |
| Automação sem planilha nem disciplina | ~36 | "o que mais me dificultava era a necessidade de ficar atualizando os gastos com certa disciplina" (avaliação com 163 votos úteis) |
| Alertas, lembretes e agentes | ~33 | "os gatinhos puxarem minha orelha", "me avisa oq da ou não" |
| Categorização boa | ~31 | "categorização afiada", "pouca coisa eu preciso alterar" |
| Open Finance no plano grátis | ~6 explícitos | "o primeiro app que encontrei com open finance gratuito" |

### 4.4 Leitura

O que segura o usuário é **não precisar lançar nada** e **ser avisado do que importa** (duplicadas, assinaturas, limite). O que o faz ir embora é **não confiar no número**:

- fatura que não bate com o banco;
- transferência e investimento contados como gasto;
- correção de categoria que não persiste;
- dado atrasado apresentado como atual.

A IA conversacional é a promessa central, mas nas avaliações aparece mais como fonte de erro do que como valor. Vários usuários comparam com mandar o extrato ao ChatGPT ou ao Claude.

---

## 5. Mapeamento contra as sub-issues #114 a #120

### 5.1 #114: Cliente Pluggy, schema `spending` e sync automático

**Já coberto**
- Cadência diária com sync ao abrir quando o último tem mais de 24 h. É o mesmo ritmo do Pierre (1x/dia) [F][9]. Também evita a armadilha do limite de sync manual, já que a Meu Pluggy nem permite refresh manual.
- Falha que não bloqueia a análise.
- Upsert idempotente.
- Troca das PENDING a cada sync.

**Sugere acrescentar ou mudar**
- **Mostrar a data do último dado.** "Dados até dd/mm hh:mm" por conta, no resumo e no modo Gastos, e aviso terso quando o sync falhou ou o item está com `LOGIN_ERROR`, `OUTDATED` ou `WAITING_USER_ACTION`. É a reclamação 3.11: dado velho mostrado como atual.
- **Deduplicar por `providerId` além do id da Pluggy.** Na renovação anual do consentimento, o itemId pode mudar (discovery da #112), e a Pluggy diz que um item novo vem com ids novos sem deduplicação entre itens [F][30]. Sem isso, o upsert pelo id não impede o dobro depois da renovação. É a principal causa de "saldo dobrado" e "transações duplicadas" no Pierre (3.8).
- **Aviso de consentimento perto de vencer** (`consentExpiresAt`), com antecedência. O Pierre não tem "reconectar" e obriga a apagar os dados para reconectar (2.1). No bogle, reconectar não pode apagar o histórico nem as correções manuais.
- **Janela do sync no futuro.** O `dateTo` precisa ir até o fim das parcelas futuras (no Nubank, até jul/2027). Senão a troca das PENDING apaga parcelas que só não foram pedidas.
- **Tratar `transactions/deleted` e os sumiços.** Se o banco apaga e recria uma transação, a correção manual feita na antiga não pode se perder sem aviso. Guardar a correção também por chave estável (`providerId`, ou data + valor + descrição) para reaplicar.

**Riscos e armadilhas**
- Para a Pluggy, PENDING cobre compra da fatura aberta, parcela futura e agendamento da conta corrente [F][30]. Trocar "todas as PENDING" só é seguro se o mesmo sync trouxer de volta as de todos esses tipos.
- Sync parcial (uma conta atualizada e outra não) deixa o casamento de fatura (#115) incoerente. Vale não recalcular o casamento quando um dos lados falhou, ou marcar o resultado como provisório.

### 5.2 #115: Regras de exclusão (transferência, pagamento de fatura, estorno)

**Já coberto**
- As três regras centrais são as do blog do próprio Pierre [F][14] e as que o produto dele mais erra (3.1, 3.2, 3.4). Gravar o motivo e recalcular a cada sync é exatamente o que falta lá.

**Sugere acrescentar ou mudar**
- **Classificação fechada, com motivo, em vez de só gasto, receita ou excluída.** Algo como gasto, receita, transferência própria, pagamento de fatura, aplicação/resgate, estorno/abatimento e ignorada (manual). O Pierre mistura essas naturezas (3.2, 3.9, 3.10).
- **Aplicação e resgate não são receita nem gasto.** Incluir `RESGATE_APLIC_FINANCEIRA` e afins (resgate automático, "guardar dinheiro") como movimento interno. É a falha relatada em 3.9.
- **Transferência própria pelo CPF**, além de "Same person transfer" e do casamento entre contas conectadas. Comparar `paymentData.receiver.documentNumber` (e `payer`) com o CPF do usuário em configuração [F][30]. Isso pega a ida para corretora ou banco não conectado, onde o Pierre falha (3.2). Ver também a ideia 6.2: Pix para a própria corretora é aporte.
- **Casamento de fatura N:1.** A fatura (`/bills` com `payments`) pode ter vários pagamentos: parcial, antes do fechamento, ou pagamento + crédito. Casar a soma dos débitos da conta corrente com os pagamentos registrados, com tolerância de data. O critério atual ("valor igual") deixa passar os casos de 3.1.
- **"Ignorar transação" manual,** reversível e com motivo visível. Era o recurso mais usado no Pierre para corrigir o que a regra não pega, e tirá-lo gerou reclamação (2.5).
- **Duplicidade como sugestão.** Marcar candidatas (mesmo valor, descrição e data, ids diferentes) com aviso terso, sem excluir. Dois Pix iguais no mesmo instante podem ser legítimos (3.3).
- **Crédito que abate despesa.** Reembolso de terceiro (Pix de quem divide o aluguel) pode ser classificado num grupo de despesa e abater como o estorno (3.5). Pode viver na #116 como regra.

**Riscos e armadilhas**
- Pix ou boleto pago com cartão via carteira (PicPay, Mercado Pago) aparece no cartão e às vezes na conta: dobro ou taxa repetida (3.3).
- Estorno de compra parcelada pode vir como crédito total ou por parcela.
- Estorno parcial.
- `CASHBACK` e `TARIFA` no cartão [F][30]: decidir se cashback é receita ou abatimento e se tarifa e IOF são gasto (sugestão: grupo "Custos bancários").
- A Pluggy cria `PAGAMENTO_FATURA` sintético quando o banco não informa [F][30]. Conferir que não vira um terceiro lado no casamento.
- Um "Same person transfer" mal classificado pela Pluggy esconde gasto real. Vale permitir desfazer a exclusão por transação.

### 5.3 #116: Grupos de categorias, correção manual e regras

**Já coberto**
- Regra determinística por CNPJ ou descrição, precedência correção > regra > Pluggy, correção que sobrevive ao sync e aplicação retroativa.
- É a resposta direta à maior frustração de categorização no Pierre. Lá a correção vale só para as transações listadas, e "compras futuras no mesmo lugar não mudam automaticamente" [F][11]. Usuários pedem justamente "criar regras para que essas classificações se mantenham" [R][2].

**Sugere acrescentar ou mudar**
- **Regra por documento do favorecido** (CPF/CNPJ do `paymentData` em Pix e TED), não só pelo CNPJ do merchant. O merchant veio em só 89 de 315 transações da conta e 47 de 235 do cartão (discovery da #112), e Pix para pessoa física não tem merchant. É o caso "Pix identificado com nome entrando como serviços" (3.3).
- **Tipo de casamento explícito na regra de descrição:** exata, prefixo ou "contém" com limite de palavra. "mercadobelo" casou com "adobe" no Pierre [R][2].
- **Pré-visualizar quantas transações mudam** antes de aplicar a regra às existentes. O Pierre faz isso, e é um bom padrão [F][11]. Também permitir desfazer: remover a regra devolve as transações à categoria anterior, menos as que têm correção manual.
- **Nunca aplicar em massa por valor sozinho.** O caso "todas de 950,00", que mudou 10 em vez de 4 [R][3], mostra o risco de critério fraco.
- **Tipo no grupo:** despesa, receita ou movimento interno (ou "não entra em análise"). Assim receitas não se misturam com gastos na lista de grupos, um crédito num grupo de despesa abate, e o resumo separa os blocos (3.10).
- **Escopo opcional da regra** por conta ou por sinal (débito ou crédito). O mesmo favorecido pode ser despesa num sentido e reembolso no outro.

**Riscos e armadilhas**
- Duas regras que casam a mesma transação: definir uma ordem determinística (por exemplo documento > CNPJ > descrição exata > descrição contém, ou prioridade explícita) e mostrar qual regra classificou.
- Aplicação retroativa muda meses fechados e o orçamento passado. Vale o aviso "N transações em M meses".
- A Pluggy pode reclassificar a própria categoria entre syncs. Isso só afeta o terceiro nível da precedência, mas vale registrar a categoria original para auditoria.
- Remover grupo exige destino (já previsto). Fazer o mesmo para grupos que são alvo de regra.

### 5.4 #117: Gastos por grupo e categoria, e tendência mensal

**Já coberto**
- Resumo por grupo com abertura por categoria, receitas à parte, filtro por conta e mês sem dado como zero.
- A visão unificada de cartão + conta corrige uma queixa do Pierre: "os gastos do cartão ficam separados dos outros gastos, portanto quando olho o que foi gasto por categoria não tenho ideia de quanto já gastei" [R][2].

**Sugere acrescentar ou mudar**
- **Comparação justa no mês corrente.** A queixa: "ele considera o total gasto no mês anterior e compara com oque já foi gasto no mês corrente, essa forma está errada e não ajuda. O correto seria comparar com o saldo gasto no mesmo período" [R][2, 16/05/2026]. Para o mês em andamento, comparar o acumulado até o dia D com o acumulado até o mesmo dia nos meses anteriores, ou mostrar uma projeção de fim de mês (ritmo). O "ritmo de gastos" foi um dos poucos recursos de tela elogiados no Pierre [R][3].
- **Mês parcial marcado.** O mês corrente e o primeiro mês com dado (histórico curto, conta desde jan/2026 e cartão desde mar/2026) não devem entrar na média sem aviso. Vale usar mediana ou excluir o mês parcial da base.
- **Visão explícita no cabeçalho:** "por data da transação". O Pierre confunde o usuário misturando a visão por compra com a por fatura (3.7).
- **Receita, gasto e sobra na mesma tela.** Um usuário pede "mostrar o saldo no mês (receitas − despesas)" [R][3]. Liga com a ideia 6.1.

**Riscos e armadilhas** (o primeiro pede uma decisão antes de implementar)
- **Parcelas na visão por data da transação.** Cada parcela da Pluggy é uma transação com data própria e `purchaseDate` da compra original [F][30]. "Por data da transação", uma compra de 10x aparece diluída em 10 meses (visão de caixa). "Por data da compra", o total cai no mês da compra. São números diferentes para o mesmo mês, e a #117 precisa escolher e dizer qual usa. A visão de caixa combina melhor com orçamento mensal [I].
- Médias puxadas por gastos únicos (IPVA, viagem). Uma marca de "não recorrente" fica para depois, mas vale prever no schema.

### 5.5 #118: Faturas e parcelas com projeção

**Já coberto**
- Fatura fechada conferida com o `totalAmount` (com diferença explicada), fatura aberta, projeção pela data de fechamento e lista de parcelas em andamento.
- A conferência atende a dor número um de confiança no Pierre: valor da fatura diferente do banco, cerca de 30 relatos (4.2).

**Sugere acrescentar ou mudar**
- **Estado "fechada, a vencer"** entre fechamento e vencimento, separado de "aberta" e "paga". A API do Pierre não tem esse estado (2.8), e usuários veem fatura vencida como se fosse a atual.
- **Fechamento vem da Pluggy** (`billClosingDate`) e o `billId` liga transação e fatura. Isso evita o chute "vencimento − 7 dias" do Pierre, que só acerta no Nubank. Para a fatura ainda sem `/bills`, usar o fechamento do ciclo anterior mais um mês, com override manual por cartão, como no Pierre (2.8).
- **Na fatura fechada, mostrar total da Pluggy, soma das transações, diferença e pagamentos** (quanto e quando, inclusive parcial e antes do fechamento). Responde 3.1.
- **Fatura aberta marcada como parcial,** com a data do último dado. O Pierre diz "aproximado", o que é honesto, mas não diz até quando.
- **Projeção sem dobro.** Parcela futura com `billId` ou já dentro da fatura aberta não entra de novo na projeção (queixa de 3.6).
- **Parcelas em lista mês a mês** (paga ou a pagar), e não só percentual. A troca por barra de progresso foi uma das remoções mais reclamadas no Pierre [R][3].
- **Comprometido por mês:** soma das parcelas futuras por mês. É o número que o blog do próprio Pierre manda olhar antes de parcelar ("cada parcela ocupa parte do teto em todos os meses em que aparece") [F][18]. Liga com a #119.
- **Cartão adicional ou virtual** (`cardNumber` diferente da conta [F][30]): pelo menos não quebrar, e talvez filtrar.

**Riscos e armadilhas**
- **Antecipação de parcelas:** as futuras podem continuar vindo como PENDING ou virar crédito na fatura (relato 3.6). Vale um teste com um caso real antes de confiar.
- Compra feita no dia do fechamento. No Nubank, o "melhor dia" é o próprio dia do fechamento [F][16], então a compra desse dia vai para a fatura seguinte.
- Fechamento que varia de mês a mês (Itaú, de 8 a 10 dias antes) [F][16].
- Encargos de rotativo e IOF internacional entram na fatura sem ser "compra".
- Só 8 faturas no histórico do Nubank, então a média de fatura é curta.

### 5.6 #119: Orçamento mensal por grupo

**Já coberto**
- Limite mensal por grupo, % usado, saldo, alerta de estouro, grupo sem limite sem alerta e mudança de limite valendo na hora.
- Uma tela de orçamento visível já supera o Pierre. Lá o "orçamento" é um alerta de agente, e a falta de "aba de orçamento" é reclamação recorrente (~12) [R][2][3].

**Sugere acrescentar ou mudar**
- **Limiar de proximidade.** O Pierre usa 50%, 75% e 100%, uma vez cada por período [F][7f]. Com as regras de texto do bogle (avisos tersos), sugiro dois limiares: 80% e 100%. Ou um alerta de ritmo: "% usado acima de % do mês decorrido + margem". O de ritmo avisa cedo quando importa e não incomoda no fim do mês.
- **Alerta com estado.** Avisar quando o limiar é cruzado, uma vez por mês e por grupo, guardando o histórico. O Pierre guarda (`alertHistory`) [F][7f], mas os relatos de 17 avisos repetidos mostram o custo de errar isso (3.12). Isso importa se a integração com `bogle check --notify` for aprovada, porque o check roda em launchd ou cron e repetiria a notificação a cada execução.
- **Orçamento criado com o mês já estourado:** o Pierre sugere começar no próximo período [F][7f]. Para uso pessoal, basta mostrar o estouro sem bloquear.
- **Parcelas no orçamento:** mostrar quanto do limite do mês já está comprometido por parcelas de compras antigas (liga com 5.5).
- **Total orçado vs gasto total**, incluindo grupos sem limite ("não orçado"), para não dar falsa sensação de controle.

**Riscos e armadilhas**
- Grupo com abatimentos (estorno ou reembolso) pode ficar negativo. Definir a exibição.
- Estorno que entra no mês seguinte "conserta" o mês errado. É coerente com a regra "abate no mês em que entra", mas o alerta do mês da compra já terá disparado.
- No começo do mês, com dado atrasado, o % usado parece menor do que é. Liga com o "dados até" de 5.1.

### 5.7 #120: TUI com modos Investimentos e Gastos

**Já coberto**
- Modos com pilha de telas própria, sync ao entrar, privacidade e tema nos dois modos e regras de texto.
- A pilha por modo resolve uma queixa repetida do Pierre: "o botão voltar fecha o app em vez de voltar a navegação... perdendo todos os filtros" [R][3].

**Sugere acrescentar ou mudar**
- **"Dados até"** visível no topo do modo Gastos (5.1).
- **Fluxo de correção curto:** na lista de transações, trocar a categoria e, na mesma ação, oferecer "criar regra?" com a prévia de quantas transações casam (5.3). Incluir a ação "ignorar" com motivo (5.2).
- **Filtros preservados por tela** ao voltar. No Pierre, a lista "atualiza ocultando várias outras transações" depois de uma edição [R][3].
- **Receitas, gastos e movimentos internos separados** na navegação (3.10).
- **Na tela de faturas, mostrar a diferença Pluggy x soma das transações** (5.5).

**Riscos e armadilhas**
- Edição de categoria foi o ponto que mais travou no Pierre (tela preta na 3.2.x [R][2][3]). Vale teste de TUI para trocar a categoria, criar regra e voltar.
- Desempenho de listas longas (centenas de transações por mês) no Textual.
- Não remover visões usadas sem substituto. No Pierre, cada redesenho que tirou recurso virou onda de 1 estrela (4.2).

---

## 6. Ideias fora do escopo atual

Esforço: **P** (pequeno: dias, sobre dados que o épico já terá), **M** (médio: tabela ou regra nova, toca 2–3 módulos), **G** (grande: novo fluxo de dados ou conciliação).

### Ligação entre gastos e investimentos

**6.1 Taxa de poupança e sobra do mês (P)**
- **O quê:** (receitas − gastos) / receitas por mês, e a sobra em R$, com a tendência.
- **Por quê:** é a métrica que o blog do Pierre usa para fechar o mês ("calcule a sobra: renda menos gasto classificado") [F][14], e o app não mostra. Para um Boglehead, é o número que liga gasto e aporte.

**6.2 Gasto → aporte (M)**
- **O quê:** detectar Pix e TED para conta própria em corretora (CPF próprio + ISPB de instituições de investimento) como "aporte". Mostrar quanto foi aportado no mês e oferecer `bogle suggest --amount <sobra>`. Depois, conferir com as compras registradas na carteira.
- **Por quê:** é o que nenhum app de gastos faz, e o bogle já tem o motor de sugestão. Também corrige o caso 3.2 (transferência para corretora contada como gasto).

**6.3 Reserva de emergência em meses de gasto essencial (M)**
- **O quê:** marcar grupos como "essenciais" e calcular a meta da reserva como 3 a 6 vezes a média de gasto essencial. Comparar com os ativos da carteira marcados como reserva.
- **Por quê:** o blog do Pierre define a reserva assim [F][19], mas o app não calcula. Os usuários pedem "objetivo para reserva de emergência" [R][3].

**6.4 Custo de vida real para a aposentadoria (M)**
- **O quê:** usar o gasto anual dos últimos 12 meses como base do saque desejado em `bogle withdraw` e no glide path, mostrando "patrimônio / gasto anual" (anos cobertos) e o número de independência para uma taxa de retirada escolhida.
- **Por quê:** fecha o ciclo gasto → carteira, que hoje depende de um valor digitado. Nada parecido existe no Pierre.

### Recorrências e projeção

**6.5 Recorrências e assinaturas determinísticas (M)**
- **O quê:** agrupar por favorecido (CNPJ, documento ou descrição normalizada), detectar cadência mensal ou anual com valor estável e listar cada uma com próxima data esperada, último valor e variação. Avisar quando o valor sobe e marcar como encerrada a que não veio no ciclo esperado.
- **Por quê:** é o maior "valor percebido" do Pierre (R$ 380 em assinaturas esquecidas [F][21]). As falhas dele (aba instável, canceladas que continuam listadas) vêm de não ter regra explícita (3.13).

**6.6 Saldo previsto do mês (M/G)**
- **O quê:** saldo atual + receitas recorrentes esperadas (salário detectado) − recorrências previstas − fatura a vencer − parcelas do mês. Mostra quanto ainda dá para gastar.
- **Por quê:** é o pedido mais frequente nas avaliações do Pierre (~79 menções a contas futuras, previsão e contas fixas). Exemplos: "peca ao não mostrar o saldo previsto... o saldo atual engana"; "saber quanto que eu posso gastar nos cartões e saber qual [...] o meu balanço mensal previsto" [R][2][3]. Depende de 6.5 e da #118.

**6.7 Teto do cartão e comprometido futuro (P)**
- **O quê:** teto mensal do cartão = renda líquida − contas fora do cartão − poupança desejada (fórmula do blog do Pierre [F][18]). Mostrar quanto das próximas faturas já está tomado por parcelas e o espaço que sobra.
- **Por quê:** quase grátis em cima da #118. Responde "cabe parcelar isso?".

**6.8 Início do mês financeiro configurável (M)**
- **O quê:** a configuração "meu mês começa no dia X" (dia do salário) para resumo, tendência e orçamento.
- **Por quê:** pedido explícito no Pierre (3.7). Toca todas as agregações, por isso M. Para um único usuário, talvez não valha se o salário cai perto do dia 1. Avaliar antes.

### Confiança no dado

**6.9 Conferência de saldo (P/M)**
- **O quê:** na conta corrente, comparar o saldo da Pluggy com o saldo anterior mais a soma das transações do período. Avisar quando há buraco ("faltam R$ X entre dd e dd"). O campo `balance` por transação vem em parte das instituições [F][30].
- **Por quê:** a maior dor do Pierre é não saber se falta transação ("R$ 1.200 não apareceram e eu não sei onde estão", resposta do próprio assistente citada por um usuário [R][3]). Um alerta objetivo de dado incompleto vale mais que qualquer análise.

**6.10 Gasto atípico e duplicidade provável (P/M)**
- **O quê:** transação acima de N vezes a mediana do grupo ou do favorecido, favorecido novo com valor alto, e possível duplicada. Tudo como aviso, nada excluído.
- **Por quê:** equivale aos agentes Albert, Julius e Inspetor Bugiganga, que estão entre o que os usuários mais elogiam (4.3), mas com regras explícitas e sem os falsos positivos relatados (3.3).

### Rotinas e lançamentos

**6.11 Resumo semanal e mensal no `bogle check` (P)**
- **O quê:** uma linha por destaque (grupos acima do ritmo, maior gasto da semana, fatura a vencer em 3 dias) no `bogle check --notify`.
- **Por quê:** equivale aos resumos de Galileu e Marie e ao lembrete de fatura, que são o "alguém olhando" que os usuários pagam para ter, usando o canal que o bogle já tem.

**6.12 Lançamentos manuais e contas previstas, com conciliação (G)**
- **O quê:** lançar dinheiro, vale-refeição ou conta prevista (aluguel, IPTU parcelado). Quando a transação real chega, ela casa com o lançamento previsto em vez de duplicar.
- **Por quê:** pedido frequente (~79 menções). O Pierre tentou via chat e duplicou (2.15). É G porque a conciliação é o difícil.

### Outras

**6.13 Despesa dividida (M):** marcar uma transação como dividida (parte a receber) e casar o Pix recebido. Pedido no Pierre (3.5). Sobre a #115 e a #116.

**6.14 Exportação CSV/JSON das transações classificadas (P):** pedida no Pierre [R][3]. O bogle já tem o padrão `--json` nos comandos.

**6.15 Provisão de gastos anuais (M):** IPVA, IPTU e seguro rateados por 12 como "provisão" mensal, e gastos únicos marcados para não distorcer a média. Regra do blog do Pierre: "divida custos anuais por 12 ou marque como pontuais" [F][14].

---

## 7. Fontes

Acessadas em 27/11/2026, salvo indicação.

**Pierre (oficial)**
1. Página principal: https://lp.pierre.finance/ (pierre.finance redireciona para ela)
2. App Store Brasil: https://apps.apple.com/br/app/pierre-controle-de-gastos-ia/id6749781755. Avaliações pelo feed público https://itunes.apple.com/br/rss/customerreviews/page=N/id=6749781755/sortby=mostrecent/json (349 avaliações).
3. Google Play: https://play.google.com/store/apps/details?id=io.cloudwalk.pierre&hl=pt_BR. Avaliações pelo endpoint público da página (`batchexecute`, método `UsvDTd`): 2.093 avaliações, de 23/09/2025 a 26/11/2026.
4. Ajuda, "Como o Pierre funciona?": https://ajuda.pierre.finance/pt-BR/articles/16020658-como-o-pierre-funciona
5. Ajuda, "Quais são os planos do Pierre?" (22/10/2026): https://ajuda.pierre.finance/pt-BR/articles/16035183-quais-sao-os-planos-do-pierre
6. Ajuda, "Como usar o Pierre?": https://ajuda.pierre.finance/pt-BR/articles/16035173-como-usar-o-pierre
7. Documentação da API (índice): https://docs.pierre.finance/llms.txt e OpenAPI https://docs.pierre.finance/api-reference/openapi.json
   - 7a. Get Transactions: https://docs.pierre.finance/api-reference/rest/get-transactions.md
   - 7b. Get Expensive Categories: https://docs.pierre.finance/api-reference/rest/get-expensive-categories.md
   - 7c. Get Bills, Get Bill Summary, Manage Closing Date: https://docs.pierre.finance/api-reference/rest/get-bills.md, https://docs.pierre.finance/api-reference/rest/get-bill-summary.md, https://docs.pierre.finance/api-reference/rest/manage-closing-date.md
   - 7d. MCP Tools: https://docs.pierre.finance/api-reference/mcp/tools.md
   - 7e. Get Installments: https://docs.pierre.finance/api-reference/rest/get-installments.md
   - 7f. Spending limits (create, confirm, list, status): https://docs.pierre.finance/api-reference/rest/create-spending-limit.md, https://docs.pierre.finance/api-reference/rest/confirm-spending-limit.md, https://docs.pierre.finance/api-reference/rest/list-spending-limits.md, https://docs.pierre.finance/api-reference/rest/get-spending-limit-transactions.md
   - 7g. Manual Update e Get Accounts: https://docs.pierre.finance/api-reference/rest/manual-update.md, https://docs.pierre.finance/api-reference/rest/get-accounts.md
   - 7h. Payment reminders: https://docs.pierre.finance/api-reference/rest/create-payment-reminder.md
8. Ajuda, "Como ver a fatura dos seus outros cartões?": https://ajuda.pierre.finance/pt-BR/articles/17694947-como-ver-a-fatura-dos-seus-outros-cartoes
9. Ajuda, "Como resolver problemas de conexão Open Finance?" (21/10/2026): https://ajuda.pierre.finance/pt-BR/articles/16035238-como-resolver-problemas-de-conexao-open-finance
10. Ajuda, "Como o Pierre usa e protege seus dados?" (28/10/2026): https://ajuda.pierre.finance/pt-BR/articles/17755431-como-o-pierre-usa-e-protege-seus-dados
11. Ajuda, "Como mudar a categoria de um gasto?" (21/10/2026): https://ajuda.pierre.finance/pt-BR/articles/17694932-como-mudar-a-categoria-de-um-gasto
12. Ajuda, "Como funcionam os agentes do Pierre?": https://ajuda.pierre.finance/pt-BR/articles/16020800-como-funcionam-os-agentes-do-pierre
    - 12b. "Como criar um agente personalizado no Pierre?" (21/10/2026): https://ajuda.pierre.finance/pt-BR/articles/16021492-como-criar-um-agente-personalizado-no-pierre
13. Ajuda, "Como gerenciar seus agentes?" (21/10/2026): https://ajuda.pierre.finance/pt-BR/articles/16020900-como-gerenciar-seus-agentes
14. Blog, "Como saber onde estou gastando meu dinheiro": https://lp.pierre.finance/blog/onde-estou-gastando
15. Blog, "App para controle financeiro: 6 opções comparadas em 2026": https://lp.pierre.finance/blog/app-para-controle-financeiro
16. Blog, "Melhor dia de compra do cartão": https://lp.pierre.finance/blog/melhor-dia-de-compra-cartao
17. Blog, "Como cancelar assinatura": https://lp.pierre.finance/blog/como-cancelar-assinatura
18. Blog, "Como controlar gastos do cartão de crédito": https://lp.pierre.finance/blog/como-controlar-gastos-do-cartao-de-credito
19. Blog, "Como organizar a vida financeira": https://lp.pierre.finance/blog/como-organizar-a-vida-financeira. Também lidos: https://lp.pierre.finance/blog/regra-50-30-20, https://lp.pierre.finance/blog/como-economizar-dinheiro e a carta https://lp.pierre.finance/letter (18/08/2026).

**Imprensa**

20. CloudWalk Newsroom (30/03/2026): https://www.cloudwalk.io/newsroom/pierre-turns-financial-management-into-a-simple-conversation----no-spreadsheets-no-confusing-charts
21. Estado de Minas, Mundo Corporativo (30/03/2026): https://www.em.com.br/mundo-corporativo/2026/03/7387214-pierre-usa-ia-para-simplificar-gestao-financeira.html
22. Finsiders Brasil, conteúdo patrocinado (30/03/2026): https://finsidersbrasil.com.br/?p=363654
23. Let's Money (31/03/2026, atualizado em 12/09/2026): https://www.letsmoney.com.br/noticias/cloudwalk-pierre-ia-multiagente-financas-pessoais/. E sobre o MCP (04/08/2025): https://www.letsmoney.com.br/destaque/pierre-mcp-alexa-financas/
24. Bloomberg Línea (20/08/2026): https://www.bloomberglinea.com.br/startups/dona-da-infinitepay-aposta-em-cartao-e-agente-de-ia-para-crescer-alem-das-maquininhas/
25. O Tempo (13/08/2026): https://www.otempo.com.br/economia/2026/8/13/em-um-ano-assistente-financeiro-de-ia-conquista-110-mil-usuarios-em-minas

**Reclamações e análises de terceiros**

26. Reclame Aqui, Pierre Finance: https://www.reclameaqui.com.br/empresa/pierre-finance/lista-reclamacoes/. A página devolveu 403 para leitura automática, então os dados vêm de trechos de busca. Reclamações citadas: https://www.reclameaqui.com.br/cloudwalk/dados-incorretos-via-open-finance-e-ausencia-total-de-suporte-do-pierre-finance_i-Fa4QN1mEHTDUS1/ e https://www.reclameaqui.com.br/pierre-finance/aplicativo-de-whatsapp-da-pierre-finance-apresenta-dados-incorretos-e-limita-o-uso-de-mensagens-em-plano-gratuito_15gMVV5vpqzkU3Wt/
27. Reclame Aqui, CloudWalk (todas as marcas): https://www.reclameaqui.com.br/empresa/cloudwalk/lista-reclamacoes/ (trechos de busca)
28. 27App, "Pierre Finance é confiável?" (24/09/2026): https://27app.com/produtos/pierre-finance/
29. Blog do app Bruno, concorrente que declara o conflito (18/08/2026, atualizado em 23/09/2026): https://oibruno.com.br/blog/pierre-finance-vale-a-pena
30. Pluggy, documentação de transações (para as lições ao bogle, não sobre o Pierre): https://docs.pluggy.ai/docs/transactions
31. mwm.ai, ficha de terceiros da App Store com legendas de capturas de tela. É fonte fraca, gerada automaticamente, e só foi usada para corroborar o painel: https://mwm.ai/apps/pierre-controle-de-gastos-ia/6749781755

**Buscadas sem resultado útil:** Reddit (nenhuma discussão encontrada), YouTube (nenhuma análise independente encontrada), posts do fundador no LinkedIn e no X (nada sobre regras de produto).
