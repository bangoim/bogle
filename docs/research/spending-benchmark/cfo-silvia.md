# Benchmark de gastos: CFO Silvia

Pesquisa feita em 27/11/2026 para o épico de gastos (#112, sub-issues #114 a #120). Todas as fontes foram acessadas nessa data. Nenhuma conta foi criada no produto. Tudo o que está aqui vem de páginas públicas: site, documentação oficial (as 23 páginas do índice [S61]), prompt library, termos, blog, imprensa e análises de terceiros.

## Como ler

Cada afirmação traz o tipo de evidência e a fonte. Os códigos `[Sn]` remetem à seção 7.

| Marca | Significado |
|---|---|
| **doc** | Documentação oficial (docs.cfosilvia.com) ou página institucional do site. É o nível mais confiável. |
| **img** | Screenshot da documentação oficial, com dados de demonstração (parte dos arquivos traz 25/11/2026 no nome). Mostra a interface real, mas os números são fictícios. |
| **blog** | Blog oficial. É marketing e às vezes contradiz a documentação. |
| **legal** | Termos de uso, política de privacidade ou página de transparência de IA. Descreve o que a plataforma "pode incluir", o que não prova que o recurso exista. |
| **prompt** | Prompt library oficial. Mostra análises que o chat de IA faria, não telas ou regras do produto. |
| **terceiro** | Imprensa, análises independentes, avaliações. |
| **inferência** | Conclusão minha a partir das evidências, indicada como tal. |

## Correção de premissa

O briefing tratava o CFO Silvia como um app com cartão próprio, conta digital e chat por WhatsApp. Não é o caso:

- É um produto **americano**, da Silvia, Inc. (Nasdaq: SVIA, antiga ProCap Financial). Atende investidores dos EUA com patrimônio alto e se conecta pelo Plaid [S1][S3][S57].
- **Não** tem cartão próprio nem conta digital. Também não tem WhatsApp, Pix, Open Finance brasileiro, parcelamento ou visão de fatura. Os canais de conversa são chat web, ligação de voz, iMessage/RCS/SMS, e-mail e MCP [S17][S18][S21].
- **Gestão de gastos é secundária no produto.** O próprio Silvia admite em várias páginas que não tem orçamento por categoria [S28][S30][S31][S32][S33]. O foco declarado é patrimônio, investimentos e imposto [S1][S46].

O valor deste benchmark para o bogle, então, está em quatro coisas:

1. O desenho de categorização, regras e exclusão de transferências.
2. As métricas que ligam gasto a patrimônio: runway de caixa, taxa de poupança, número de independência financeira e atribuição da variação do patrimônio.
3. O catálogo de análises de gastos que o Silvia entrega pelo chat de IA e que o bogle pode implementar como código determinístico.
4. As falhas e lacunas do produto, que servem de alerta.

Assistente de IA, chat, voz e WhatsApp estão fora do escopo do bogle por decisão do usuário. Por isso as análises "via IA" são tratadas aqui como especificações de cálculo, não como interface.

---

## 1. Visão geral do produto

### Público e posicionamento

- Slogan: "AI CFO built for serious investors". O site alega 20.000+ investidores, US$ 60 bi rastreados e patrimônio médio de US$ 2,5 mi [S1] (doc, alegação não verificada). As alegações cresceram ao longo do tempo: 10.000+/US$ 30 bi no G2 [S58], cerca de 12.000 usuários/US$ 30 bi em fev/2026 [S55], 15.000+ nas calculadoras [S48].
- Missão declarada em set/2026: "help independent investors make money" [S46] (blog).
- O marketing compara o produto a "oito pessoas": gestor de patrimônio, CFO, contador, risk officer etc. Um dos "cinco problemas" que diz resolver é "Every transaction has a story behind it": distinguir transferência entre contas próprias de saque [S1] (doc).

### Empresa

- Cofundadores: Anthony Pompliano (CEO; investidor e mídia financeira) e Shain Noor (produto e engenharia) [S4] (doc). Fundada em maio de 2025 [S55] (terceiro).
- A ProCap Financial (Nasdaq: BRR) anunciou a compra em fev/2026 [S55]. Em 22/09/2026 passou a negociar como Silvia, Inc. (SVIA) [S57] (terceiro).
- Equipe de menos de 10 pessoas em out/2026 [S60] (terceiro). Um resumo de busca cita um comunicado de 20/10/2026 sobre corte de custos de mais de US$ 10 mi por ano [S59]. Não consegui abrir o comunicado (HTTP 403), então isso não foi conferido.

### Modelo de negócio

Freemium [S2] (doc):

| Plano | Preço | O que muda |
|---|---|---|
| Free | US$ 0, sem cartão | Uso limitado de chat, ligação, SMS, e-mail, Research e Radar. Agregação, patrimônio e transações incluídos. Conexões bancárias **pausam o refresh após 30 dias de inatividade**. |
| Pro | US$ 17/mês no anual (US$ 200/ano) ou US$ 20 no mensal | Uso 10× maior, modelos de IA mais novos, "dados financeiros mais robustos", conexões ilimitadas, suporte prioritário. |
| Max | US$ 200/mês | Uso 10× o do Pro, setup pessoal com o fundador, acesso antecipado. |

- O que se cobra é **uso de IA**. Agregação, transações e patrimônio são gratuitos [S2][S29].
- Limite de portfólios: 2 no Free, até 100 no Pro e no Max [S25] (blog).

### Plataformas e canais

- **Web mobile-first** (adicionar à tela inicial). O FAQ diz que os apps nativos estão "on the near-term roadmap" [S3] (doc). Posts do blog afirmam o contrário: "Silvia has native iOS and Android apps" [S29][S35] (blog). Em 27/11/2026 não achei listagem na App Store pela API de busca do iTunes, nem no Google Play. Duas análises independentes confirmam que não há app nativo [S51][S52] (terceiro).
- Canais conversacionais: ligação de voz com IA, iMessage, RCS no Android, SMS como fallback, e e-mail [S17][S18] (legal).
- **MCP**: servidor `https://www.cfosilvia.com/mcp` para Claude, ChatGPT, Gemini, Codex, Grok e outros. Expõe só duas ferramentas: perguntar ao Silvia e buscar resposta pendente. Não exporta dados crus [S21][S22] (doc/blog).
- Integração com Gmail, Google Calendar e Google Drive, que lê e age com confirmação [S17][S18] (legal).

### Agregadores e dados

- **Bancos e cartões: Plaid**, "12.000+ bancos e corretoras dos EUA" [S3][S19] (doc). O guia de cada banco descreve o fluxo OAuth da instituição e avisa que saldos, posições e transações podem chegar em momentos diferentes depois da primeira conexão [S20] (doc).
- **Corretoras: SnapTrade**. Em ago/2026 dizia "additional providers like MX coming next" [S27] (blog).
- Cripto: Coinbase/OAuth e chaves de API somente leitura. Imóveis: Zillow. Veículos: MarketCheck. Preços: CoinGecko e CoinMarketCap. Pagamentos: Stripe. Empresa: QuickBooks (Intuit), com sincronização diária [S17][S15][S26] (legal/doc).
- Acesso somente leitura. O Silvia não move dinheiro [S3][S17].
- Histórico: "depende do que a instituição fornece" [S12] (doc). O planejador de aposentadoria usa os últimos 12 meses de transações [S34][S40] (blog).
- Frequência de atualização: não há prazo oficial. Um post genérico diz que "most connections refresh once a day" [S36] (blog, não específico do Silvia).
- Contas internacionais: dependem da instituição. Sem conexão, o caminho é entrada manual ou importação [S12] (doc).

---

## 2. Inventário de funcionalidades de gestão de gastos

### 2.1 Tela Transactions

Fontes: [S5] (doc) e screenshots `transactions__general-access.png` e `transactions__actions-general-access.png` (img).

- **Filtros globais:** portfólio, período e contas. As três seleções valem para toda a tela.
- **Três cards de resumo:** Outgoing, Incoming e **Net Cashflow**. No screenshot, Net Cashflow = Incoming − Outgoing: 232.167 − 163.423 = 68.744 (img, conta conferida).
- **"Spending by day":** calendário semanal com o total gasto por dia, número de transações ("2+") e intensidade de cor (escala "Less/More"). Mostra duas semanas e se expande com "Show full month" (img/doc).
- **Rosca por categoria**, com alternância Outgoing/Incoming. Mostra valor por categoria. No exemplo, "Uncategorized" é a terceira maior fatia: US$ 28.632 de US$ 163.423, ou 17,5% (img).
- **Lista:** busca textual, filtro de categoria, faixa de valor (de/até) e colunas Name, Category, Date e Amount, com a conta embaixo do nome. Tem botão **Bulk Edit** (img; não descrito no texto da documentação).
- **Abas:** "All transactions" e "**Subscriptions**", esta com as cobranças recorrentes (doc).
- **Menu Actions:** Add account, Add manual transaction, **Manage rules** e **Download**. O CSV exportado respeita os filtros da tela (doc/img).

### 2.2 Categorização automática

- Modelo de ML que classifica pelo merchant, pela descrição e pelo valor. "User corrections improve the model". Admite que "Transaction categorization is not 100% accurate" [S16] (legal).
- **Taxonomia de dois níveis**: categoria e subcategoria, como Food & Dining > Groceries e Food & Dining > Coffee Shops [S5] (img).
- Categorias no exemplo: Food & Dining, Shopping, Uncategorized, Travel & Lifestyle, Auto & Transport, Health & Wellness, Bills & Utilities, Gifts & Donations e **Recurring & Subscriptions** (img). A recorrência aparece como categoria própria, misturada com natureza do gasto. Ver a crítica na seção 5, #116.
- A documentação não diz se a taxonomia é fixa ou editável. Não achei como criar, renomear ou agrupar categorias.

### 2.3 Correção de categoria

O modal "Edit Transaction Category" [S5] (doc + img) tem, num só passo:

1. Categoria e subcategoria.
2. Checkbox "**Mark as transfer between my accounts (will be excluded from income/spending calculations)**". A exclusão é um atributo separado da categoria.
3. Checkbox "**Create rule to auto-categorize similar transactions**".

O cônjuge convidado também pode corrigir categorias, e a alteração aparece para o dono [S24] (blog).

### 2.4 Regras

O modal "Manage Transaction Rules" tem as abas "My Rules" e "Create Rule" [S5] (doc + img `transactions--rules--4k.png`).

- Nome da regra.
- **Condições combinadas com E**: "A transaction has to meet every one you check". As condições são:
  - Transaction name (operador, ex. "contains", + texto);
  - Merchant name;
  - Amount;
  - Accounts. Sem marcar contas, a regra vale para todas.
- **Ação:** um dropdown "Action" com "Categorize as" visível, mais categoria e subcategoria. Não dá para ver se há outras ações, como marcar transferência.
- **"Preview matching transactions"** antes de salvar.
- O subtítulo diz que as regras categorizam "as they arrive". A documentação **não diz** se a regra reclassifica transações antigas. Também não fala de ordem de precedência entre regras que conflitam.

### 2.5 Assinaturas e recorrências

- Aba Subscriptions na tela Transactions [S5][S13] (doc).
- O Radar pode vigiar "recurring bills, subscriptions, or unusual cash outflows". Exemplo do post: um serviço que sobe de US$ 40 para US$ 55 por mês soma US$ 180 por ano [S23] (blog).
- A página de transparência lista "recurring charges, subscription tracking, expense reduction opportunities" por "pattern recognition algorithms applied to transaction history" [S16] (legal). O algoritmo de detecção não é descrito.
- **Não cancela assinaturas nem negocia contas** [S29][S34] (blog).

### 2.6 Lançamento manual e importação

- Lançamento manual só em **conta manual** de cartão ou banco: Debit para cobrança, Credit para estorno ou reembolso [S5] (doc).
- **Importação de CSV ou PDF**: no CSV, Date, Description e Amount, com mapeamento de colunas e pergunta sobre o sinal ("whether positive amounts represent spending or income"). O PDF de extrato é lido por IA. Há revisão antes de importar [S5] (doc + img).
- Risco documentado: **duplicata** quando a mesma transação chega pela conexão e pelo extrato importado. A orientação é remover a cópia manual [S11] (doc).

### 2.7 Dashboard e fluxo de caixa

Fontes: [S6] (doc) e `dashboard--cash-flow.png` (img).

- Widgets que podem ser arrastados ou removidos: patrimônio, caixa total, dívida total, investimentos, fluxo de caixa (Outgoing/Incoming), transações recentes e **Cash Runway**.
- **Cash Runway** exibe "Monthly Burn" (US$ 8.894), "Cash On Hand" (US$ 861.262) e o runway em anos (8,0). *Inferência:* runway = caixa ÷ gasto mensal. 861.262 ÷ 8.894 = 96,8 meses ≈ 8,07 anos, o que bate com o número exibido. O blog define runway como "how many months of spending your cash covers" [S37]. A documentação não diz como o "monthly burn" é calculado (janela de meses, exclusões).
- Períodos: 1D, 7D, 30D, 90D, 6M, YTD, 1A e All Time.
- Banner de **Reconnect** quando uma conexão precisa de nova autenticação.

### 2.8 Metas

Fontes: [S7] (doc) e `goals__create.png`, `goals__general-access.png` (img).

- Tipos: **Grow, Save, Pay Off, Invest**.
- **Fonte** acompanhada: Net Worth, Total Cash, Total Debts ou uma conta específica. Alvo em valor ou em "% of Source". Data alvo.
- O card mostra % concluído, quanto falta, data alvo e "**$X/mo to hit target**". *Inferência pelos números do screenshot:* é a divisão linear do que falta pelos meses até a data, sem rendimento. "Save for a house": faltam US$ 342.331 até 16/02/2034, cerca de 87 meses ≈ US$ 3.935/mês; o card mostra US$ 3.941.
- Status "On track" e "Complete". **Metas sugeridas**, como "Debt-Free" a partir do total de passivos.
- *Inferência (img):* várias metas usam a **mesma fonte** sem separar dinheiro. A meta "Save up for a new car" (alvo de US$ 20 mil, fonte Net Worth) aparece como concluída só porque o patrimônio total passa de US$ 20 mil, e outras duas metas acompanham o mesmo patrimônio. Não há reserva de dinheiro por meta (earmarking).
- "Settings → Goals" guarda prioridades qualitativas para personalizar a IA. A página Goals guarda as metas numéricas [S7][S10].

### 2.9 Radar (alertas agendados)

Fontes: [S8] (doc), [S23] (blog) e `radar__general-access.png` (img).

- O usuário descreve em linguagem natural o que vigiar e a condição de alerta, escolhe cadência (diária, semanal ou mensal) e horário, e o canal: e-mail, SMS ou push.
- O feed tem filtros All, Unread, Bookmarked e Skipped. Dá para pausar (Active), editar e fazer perguntas de seguimento.
- Sugestões da própria tela ligadas a gastos: "Alert me on any expense above two thousand dollars" e "**Warn me when runway drops below six months**" (img).
- Usos de gastos citados: recorrências, saídas incomuns, "Cash management" (caixa disponível contra despesas futuras, obrigações recorrentes e colchão de liquidez) e ritmo de contribuição contra as metas [S23] (blog).
- É verificação por IA, não regra determinística. O SMS pode conter saldos e transações sem criptografia ponta a ponta [S18] (legal).

### 2.10 Análises de gastos pelo chat (prompt library)

Não são telas. São prompts prontos que o chat (Ask Silvia [S9]) executa sobre os dados conectados [S47] (prompt). Para o bogle, servem como **especificação de análises** que podem virar código.

| Prompt | O que pede (resumo fiel) |
|---|---|
| Check my spending every week | Radar aos domingos: semana atual contra a "semana típica"; marcar **estabelecimentos novos**, cobranças grandes fora do padrão e categorias acima do normal; dizer **quanto ainda dá para gastar no mês sem perder a meta de poupança**. Seguimentos: excluir aluguel/financiamento; só avisar quando houver algo incomum. |
| Understand where the money went | Últimos 3 meses contra os 3 anteriores; categorias e **estabelecimentos** com as maiores variações; separar compra pontual de mudança recorrente; marcar o incomum. |
| Detect lifestyle creep | Todo dia 1: 3 meses móveis contra o mesmo período do ano anterior, por categoria; separar pontual de mudança de base recorrente; estimar o efeito na **taxa de poupança** e na data de independência financeira, com premissas explícitas. |
| Find recurring expenses worth reviewing | Listar recorrências com **custo anualizado**, agrupar em essencial, útil e revisar; marcar aumento de preço, duplicatas e serviços pouco usados. |
| Watch my subscriptions | Radar mensal: recorrência que subiu, assinatura nova, trial que virou pago, mesmo serviço cobrado duas vezes. Seguimentos: o que renova em 30 dias; total anual. |
| Turn my spending into a budget spreadsheet | 12 meses de transações em planilha com categorias × meses, **média de 12 meses**, coluna do mês atual contra a média e marcação de recorrente e pontual. Seguimento: "Suggest a target for each category". |
| Set a safe spending number | Quanto dá para gastar por mês dados renda, custos fixos, dívidas, metas de poupança e colchão; faixa conservadora e flexível. |
| Build a cash buffer | Reserva de emergência pelas **obrigações mensais reais**, estabilidade da renda e ativos acessíveis; situação atual, lacuna e plano mensal. |
| Audit idle cash | Proteger 3 meses de **gasto essencial médio** e comparar o caixa excedente com o rendimento disponível. |
| Use excess cash deliberately | Caixa acima da reserva: manter, amortizar dívida ou investir. |
| Explain the last 90 days | **Atribuir a variação do patrimônio** a mercado, poupança, gasto, dívida e contas novas ou reavaliadas. |
| Monitor my financial-independence date | Radar mensal que recalcula a data de independência financeira pela taxa de poupança e pelo gasto atuais, com faixa e não data única, e explica o maior fator da mudança. |
| Send me a monthly report with a spreadsheet | Todo dia 1: patrimônio, fluxo de caixa e variação de cada conta, com Excel anexo. |
| Build my financial command center | As cinco métricas a acompanhar todo mês, com o valor de hoje e o que seria sinal de alerta. |
| Find one thing I haven't noticed | Radar semanal: uma coisa em carteira, gastos, caixa ou dívida que provavelmente passou despercebida, com o impacto em dinheiro. |

### 2.11 Relatórios periódicos e notificações

- Notificações de conta: **Daily summary** e **Personal financial statement** com periodicidade desligada, mensal, trimestral ou anual [S10] (doc).
- Push para respostas do chat, Research e alertas do Radar [S10] (doc).

### 2.12 Outros recursos relacionados

- **Multi-portfólio:** cada conta pertence a um só portfólio, e o seletor filtra Dashboard e Transactions [S25] (blog).
- **Multi-moeda:** guarda o valor na moeda original e converte para a moeda de exibição. O histórico de câmbio começa em 04/03/2026 [S26] (blog).
- **Acesso compartilhado:** um convidado com login próprio, sem modo somente leitura e sem esconder portfólios [S24] (blog). Fora de escopo para o bogle, que tem um único usuário.
- **Evitar contar duas vezes no patrimônio:** é um tema recorrente. Holdings importados contra saldo manual [S14], contas de uma empresa do QuickBooks já conectadas à parte [S15] e financiamento ligado ao imóvel [S6] (doc). O princípio é o mesmo do casamento de fatura da #115.

### 2.13 O que não existe (ou não foi encontrado)

| Recurso | Situação | Fonte |
|---|---|---|
| Orçamento por categoria/envelopes | **Não existe**, e o produto admite: "doesn't ask you to build a budget (yet)" | [S28][S30][S31][S32][S33] (blog) |
| Alerta de estouro de orçamento | Citado em "Alerts and Nudges: … budget overruns" [S16] (legal), **em contradição** com a ausência de orçamento. Provavelmente texto genérico ou plano futuro (inferência). | [S16] |
| Cancelamento de assinatura e negociação de contas | Não faz | [S29][S34] |
| Conciliação de contas, registro de cheques | Não faz | [S31] |
| Visão de fatura de cartão (ciclo, fechamento, vencimento) | Não encontrado em nenhuma página | busca em todo o site e na documentação |
| Parcelas | Não encontrado (o produto é dos EUA) | idem |
| Casamento de estorno com a compra original | Não encontrado. Só lançamento manual de crédito. | [S5] |
| Casamento automático de pagamento de cartão com o débito na conta | Não documentado | idem |
| Transações pendentes contra lançadas | Não documentado | idem |
| Regras com ação de exclusão (marcar transferência) | Não visível. Só "Categorize as" aparece. | [S5] (img) |

---

## 3. Regras de negócio e casos difíceis

| Caso | O que o Silvia faz | Evidência | Lacunas e falhas |
|---|---|---|---|
| **Transferência entre contas próprias** | A IA classifica, e o usuário pode marcar manualmente "transfer between my accounts", que sai de receitas e gastos | [S1][S5][S36] (doc/blog) | Não há regra de casamento documentada. O próprio blog admite que, se o sistema errar, "your spending looks $50,000 higher" e que transferências mal rotuladas "can inflate both sides of your cash flow" [S36]. |
| **Pagamento de fatura do cartão** | Não documentado | — | Inferência: o produto depende da categoria do Plaid e da marcação manual. Sem casamento explícito, existe o mesmo risco de contar o gasto duas vezes que a #115 resolve. |
| **Estorno/reembolso** | Só em conta manual: lançamento "Credit" para reembolso | [S5] (doc) | Não casa estorno com a compra nem abate da categoria original. |
| **Recorrências** | Aba Subscriptions, categoria Recurring & Subscriptions, Radar de aumento | [S5][S23] | Algoritmo não descrito. Recorrência como categoria compete com a categoria natural do gasto (ex.: streaming em Lazer). |
| **Receitas** | Card Incoming e rosca de Incoming por categoria. Análise de holerite pelo chat. | [S5][S47] | Não há separação documentada entre salário, rendimento e reembolso. |
| **Sem categoria** | "Uncategorized" é categoria explícita nos gráficos | [S5] (img) | No exemplo oficial, 17,5% das saídas ficam sem categoria. A qualidade da categorização aparece como fatia, mas o produto não a trata como pendência. |
| **Duplicatas** | Orienta comparar conta, valor, data e origem e apagar a cópia manual | [S11] (doc) | Deduplicação manual. |
| **Conexão quebrada ou desatualizada** | Banner de Reconnect. Pede para comparar o saldo com o do banco. | [S6][S11] (doc) | No Free, o refresh pausa após 30 dias de inatividade [S2]. Avaliação: conexão com a Fidelity parada por mais de uma semana e suporte sem resposta [S50]. O blog recomenda "always check when each account last refreshed" [S36]. |
| **Histórico curto** | "Depends on the history your institution supplies" | [S12] | Nenhum tratamento de cobertura nas séries. |
| **Contas fora do agregador** | Conta manual, importação de CSV ou PDF | [S5][S12] | Risco de duplicata se a conta for conectada depois. |
| **Metas na mesma fonte** | Várias metas acompanham o patrimônio total | [S7] (img) | Meta pode aparecer como concluída sem nenhum dinheiro reservado para ela (inferência, ver 2.8). |
| **Cálculo e IA** | "Deterministic calculations combined with AI-driven pattern recognition" | [S16] (legal) | O blog defende que "a good system does this math with ordinary code, not with the language model" [S36]. Mesmo assim, boa parte das análises de gastos só existe pelo chat (2.10). |

---

## 4. Dores dos usuários

### Base de evidências (fraca)

- **Lojas de apps:** não há app nativo nem listagem (seção 1). Não existem avaliações de loja.
- **Product Hunt:** 1 avaliação, média 2,0 [S50].
- **G2:** 0 avaliações [S58]. **Trustpilot:** sem página.
- **Reddit:** existe o r/CFOSilvia, mas o acesso automatizado ao Reddit foi bloqueado e a busca não indexou nenhuma thread. **Não conferido.**
- **Página de depoimentos** [S49]: 126 tweets escolhidos pela empresa. Mostram o que o público valoriza, mas não as dores.
- Comentários avulsos: newsletter do fundador [S56], fórum White Coat Investor e LinkedIn citados por [S51].

### Reclamações e pedidos

| Tema | Ocorrências | Fonte |
|---|---|---|
| Sincronização: no lançamento só pegava saldo ("syncs snapshot level information only and does not yet do transactions") e a conexão com a Fidelity ficou parada mais de uma semana | 1 | [S50] |
| Suporte sem resposta durante a falha | 1 | [S50] |
| Privacidade ao conectar tudo ("who knows where it winds-up"; "linking every account is what gives him pause") | 1 direta, 1 relatada por terceiro, 1 análise | [S56][S51][S53] |
| Falta de app nativo ("When can we anticipate the app version?!") | 1 pedido + 2 análises | [S49][S51][S52] |
| Falta de orçamento por categoria | 2 análises + admissão do próprio produto | [S52][S30][S31] |
| Bancos de fora dos EUA; integração com o TSP (previdência federal) | 1 + 1 | [S49] |
| App web lento no celular (Lighthouse 52, LCP 13,4 s) | 1 análise | [S54] |
| Alegações de tração e certificação SOC 2 sem verificação independente | 3 análises | [S51][S53][S54] |
| Produto exige ler os alertas ("Radar helps only if you read the findings") | 1 análise | [S51] |

### Elogios (depoimentos selecionados pela empresa, 126 no total)

Contagem aproximada por tema, com palavras-chave e revisão manual dos trechos sobre gastos:

- Investimentos e carteira (rebalanceamento, simulações, Monte Carlo, opções): ~29.
- Imposto (planejamento, erros na declaração, tax-loss harvesting): ~20.
- Consolidação de tudo em um lugar: ~11.
- **Gastos: ~7.** Os temas são:
  - "found 4 monthly's I didn't realize I still had… save me over $2000/year": **assinaturas esquecidas**;
  - "check how much I'm spending on DoorDash": gasto por estabelecimento;
  - "Got my budget made" e "use it to stay on budget";
  - "Every month i use Silvia to make a financial report that i review with my wife on our spending, savings goals": **revisão mensal**;
  - "I was hand math on upcoming large bills… Silvia caught [an error] to keep me cash flow positive this month": **contas futuras grandes contra caixa**;
  - visão de ativos, renda e despesas.
- Troca de concorrente (Monarch, Empower/Personal Capital, Wealthfront): 4.

**Leitura** (inferência): o público usa o Silvia sobretudo para imposto e investimentos. Quando fala de gastos, elogia três coisas, e nenhuma delas é orçamento: achar recorrências esquecidas, fazer uma revisão mensal consolidada e comparar compromissos futuros com o caixa. O orçamento aparece em 2 de 126 depoimentos, mesmo o produto não tendo orçamento formal.

---

## 5. Mapeamento contra as sub-issues

### #114 (15.1) Cliente Pluggy, schema e sync automático

**Já coberto pela issue:** sync idempotente por id, substituição das parcelas futuras, sync automático com mais de 24 h, falha que não bloqueia a análise.

**O Silvia sugere acrescentar:**

- **Mostrar a data do último sync bem-sucedido por conta**, não só por item. O Silvia mostra banner de reconexão, e o blog dele insiste em "check when each account last refreshed" [S36]. Em terminal, isso pode ir no rodapé do resumo e da TUI.
- **Alertar no `bogle check`** quando o consentimento estiver perto de expirar (`consentExpiresAt`, um ano) ou quando o item estiver com erro na Pluggy. Uma conexão parada sem aviso é a principal reclamação real sobre o Silvia [S50].
- **Guardar a data da primeira transação disponível por conta** (cobertura). A #117 usa isso para não confundir "sem dados" com "zero". Ver abaixo.

**Riscos:**

- O itemId pode mudar na renovação do consentimento (discovery da #112). *Inferência:* se os ids de conta e de transação também mudarem, o upsert por id da Pluggy duplica o histórico. Vale prever deduplicação por chave natural (conta lógica, data, valor, descrição) ou uma migração de ids na renovação.
- Importação manual paralela, se um dia existir um fallback por CSV, gera duplicatas. O Silvia documenta esse problema [S11].

### #115 (15.2) Exclusão: transferências, pagamento de fatura, estorno

**Já coberto pela issue:** marcação de gasto, receita ou excluída com motivo; casamento de fatura nos dois lados; estorno abatendo a categoria original. Nisso o bogle vai **além** do Silvia, que não documenta casamento de fatura nem de estorno.

**O Silvia sugere acrescentar:**

- **Override manual de exclusão por transação**, independente da categoria. É o checkbox "Mark as transfer between my accounts" do Silvia [S5]. A #115 só prevê regras automáticas. Falta um jeito de o usuário dizer "isto é transferência minha" ou "isto não é transferência" quando a regra erra, com precedência manual > regra > automático, como na #116.
- **Não excluir a categoria "Transfers" inteira.** Pix para terceiros (aluguel, diarista, divisão de conta) costuma vir como "Transfers" e é gasto. A exclusão precisa olhar a contraparte: mesmo CPF/CNPJ do titular ou conta própria conhecida, como no "Same person transfer". *Inferência baseada no discovery:* boa parte do volume da conta corrente é "Transfers".
- **Separar "excluída" de "aporte".** `RESGATE_APLIC_FINANCEIRA` e aplicações na própria instituição não são gasto nem receita, mas o débito de uma aplicação é **poupança**. Gravar esse motivo próprio (ex.: `investimento`) habilita a taxa de poupança e a ligação gasto→aporte da seção 6.
- **Regra de exclusão criada pelo usuário**: "toda transação com descrição X é transferência". O Silvia só mostra a ação "Categorize as".

**Riscos e armadilhas:**

- Pagamento de fatura **parcial**, em **duas vezes**, **antecipado** ou de valor diferente do total (pagamento mínimo). O casamento só por "valor igual" falha. O `payments` de `/bills` ajuda a casar por soma.
- Pagamento de fatura de **cartão não conectado**: o débito fica sem par. Precisa de motivo próprio ("pagamento de cartão externo") e decisão de produto: excluir e perder o gasto, ou contar como gasto agregado.
- Estorno **parcial**, estorno que cai em **fatura posterior** e estorno de compra **parcelada** (abate parcelas futuras ou o total?). Pix devolvido.
- Duas transações de mesmo valor na mesma janela: casamento ambíguo. Gravar o par casado (id da contraparte) e não só a marca, para auditar.

### #116 (15.3) Grupos, correção manual e regras

**Já coberto pela issue:** padrão no banco, renomear e remapear, "Outros" com aviso, precedência correção > regra > Pluggy, regra aplicável às transações existentes. Nesse último ponto o bogle vai além do Silvia, cuja documentação não garante retroatividade.

**O Silvia sugere acrescentar:**

- **Condições de valor (faixa) e de conta** nas regras, combinadas com E, além de CNPJ e descrição [S5]. Exemplo: "PIX enviado para fulano acima de R$ 1.000 na conta X = Moradia".
- **Prévia antes de salvar ou aplicar**, com contagem e lista das transações afetadas ("Preview matching transactions"). Isso evita regras amplas demais, como "contém PIX".
- **Correção, marcar transferência e criar regra num único passo** de interface [S5].
- **Edição em lote** (Bulk Edit).
- **Indicador de qualidade**: % do gasto do mês em "Outros" ou sem grupo. O Silvia mostra "Uncategorized" como fatia de 17,5% sem tratá-la como pendência.
- **Atributo de natureza por grupo**: essencial, discricionário ou poupança. É o "needs, wants, savings" da calculadora de orçamento do Silvia [S48] e o "essential, useful, review" dos prompts [S47]. Sem esse atributo não dá para calcular a reserva em meses de gasto essencial (seção 6).

**O que evitar:**

- **Recorrência como grupo** (o Silvia tem "Recurring & Subscriptions" como categoria). Recorrência é atributo da transação. Um streaming é Lazer *e* recorrente. Se virar grupo, ele concorre com os demais e distorce o resumo.

**Riscos:**

- Conflito entre regras: definir a ordem (mais específica ou mais recente) e mostrar qual regra classificou cada transação.
- O merchant só vem em parte (89/315 na conta, 47/235 no cartão, segundo o discovery). Regra por CNPJ cobre pouco e a regra por descrição vira o padrão. A descrição muda entre bancos e meios de pagamento, então vale normalizar (caixa, espaços, prefixos como "PIX ENVIADO").

### #117 (15.4) Gastos por grupo e tendência

**Já coberto pela issue:** total por grupo com abertura por categoria, comparação com a média, série mensal, filtro por conta, receitas à parte.

**O Silvia sugere acrescentar:**

- **Saldo do mês (receitas − gastos) e taxa de poupança** no resumo. É o "Net Cashflow" do Silvia, e o blog dele define a taxa de poupança como (renda líquida − gasto) ÷ renda líquida [S37].
- **Variação por estabelecimento**, não só por grupo: "categories and merchants responsible for the largest changes" [S47].
- Comparar com o **mesmo mês do ano anterior** quando houver histórico, além da média recente [S47].
- **Separar pontual de recorrente** na explicação da variação [S47].
- Opcional: **calendário de gasto por dia**. Mas o heatmap do Silvia depende de legenda ("Less/More"), o que conflita com a regra de interface sem legendas. Se entrar, que mostre números.

**Riscos e armadilhas:**

- **"Mês sem dados aparece como zero"** está certo dentro da cobertura, mas errado **antes** dela. O cartão do Nubank só tem dados desde mar/2026. Jan e fev em zero puxariam a média para baixo e criariam uma falsa alta. A série deve começar no primeiro mês coberto pelas contas filtradas, ou marcar os meses fora da cobertura.
- **Mês corrente parcial** contra média de meses cheios dá falsa queda. Comparar até o mesmo dia do mês ou projetar o ritmo do mês.
- **Parcelas e "data da transação":** nas parcelas da Pluggy, a data da transação é a da parcela e `purchaseDate` é a data da compra. A issue precisa dizer qual vale. Proposta: a parcela no mês dela (visão de caixa, coerente com a fatura). O Silvia não trata o caso, então não ajuda.

### #118 (15.5) Faturas e parcelas com projeção

**Situação no Silvia:** nada. Não há visão de fatura nem de parcelas (seção 2.13). O mais perto é o conceito de "**upcoming obligations**", com impostos, mensalidades e seguros dos próximos 90 dias no painel ideal descrito pelo blog [S37][S44], e o depoimento sobre "upcoming large bills".

**O Silvia sugere acrescentar:**

- Tratar a projeção de faturas como **compromissos futuros**, um número que também aparece no resumo do mês: quanto da renda dos próximos meses já está comprometida com parcelas e faturas abertas.
- **Comprometimento de renda futura** = parcelas futuras do mês N ÷ receita média. Alerta quando passa de um limite.

**Riscos:**

- O `totalAmount` da fatura inclui encargos, IOF, juros, anuidade e créditos ou saldo da fatura anterior. Por isso o critério "diferença explicada" da issue é necessário, e a explicação precisa listar esses componentes.
- **Antecipação de parcelas:** as parcelas futuras somem do `PENDING` e aparecem como um lançamento só. A lista de parcelas em andamento precisa tolerar isso.
- Compra internacional: o valor muda com o câmbio até o fechamento.

### #119 (15.6) Orçamento mensal por grupo

**Situação no Silvia:** **não tem orçamento** e admite isso. Tem só três coisas: alerta de despesa individual acima de um valor, o "safe spending number" pelo chat e uma planilha de orçamento gerada a partir de 12 meses de gastos, com o seguimento "suggest a target for each category" [S47]. O bogle vai além do Silvia aqui.

**O Silvia sugere acrescentar:**

- **Sugestão de limite inicial** por grupo a partir da média ou da mediana dos últimos 3 a 6 meses. Reduz o atrito de começar.
- **Alerta de ritmo**: % do limite usado contra % do mês decorrido, com projeção para o fim do mês. É mais útil que um limiar fixo de 80% e responde ao "quanto ainda posso gastar no mês" do prompt semanal [S47].
- **Grupos irregulares**: IPVA, IPTU, seguros, viagens e material escolar não cabem em limite mensal fixo. Opções: limite anual, ou provisão mensal = total anual ÷ 12 [S39].
- **Alerta de despesa única acima de X** (Radar "expense above two thousand dollars").

**Riscos:**

- Excesso de alertas. O próprio Silvia recomenda **materialidade, deduplicação e cooldown** e separar alerta informativo de alerta de ação [S42].
- Sobre o ponto em aberto (alerta no `bogle check`), recomendo **sim, só para estouro (> 100%), uma vez por grupo por mês**. A proximidade fica no resumo e na TUI.

### #120 (15.7) TUI com modos

**Já coberto pela issue:** modos, resumo, tendência, faturas, lista com correção e regra, grupos.

**O Silvia sugere:**

- **Modal de correção com três ações** (categoria, transferência, regra) [S5].
- **Prévia de regra** antes de confirmar, e **seleção múltipla** para edição em lote.
- **Motivo visível** de cada transação excluída: casada com a fatura X, transferência própria, estorno de Y. Sem isso o usuário não confia no total.
- Na lista de transações, filtros por grupo, conta e faixa de valor e busca textual, como os do Silvia [S5].
- **Indicador de frescor** (último sync) no cabeçalho do modo Gastos.

**Riscos:**

- O Silvia aposta em widgets visuais (rosca, heatmap) que dependem de legenda de cor. Isso conflita com a regra de texto da interface do bogle. Prefira tabelas com números e barras.

---

## 6. Ideias fora do escopo atual

Esforço: **P**, até uns dias; **M**, uma sub-issue; **G**, várias sub-issues ou um épico. As estimativas são minhas.

| # | Ideia | Por que faz sentido | Inspiração no Silvia | Esforço |
|---|---|---|---|---|
| 1 | **Sobra do mês → sugestão de aporte.** `receitas − gastos` do mês fechado vira o valor inicial de `bogle suggest --amount`. Mostrar também "aporte efetivo": as compras registradas no bogle no mês contra a sobra. | É a ligação mais direta entre os dois módulos e transforma a análise de gastos em ação de investimento. Exige o motivo `investimento` da #115. | "Use excess cash deliberately", "Explain the last 90 days" [S47] | P |
| 2 | **Taxa de poupança mensal e móvel de 12 meses.** (receitas − gastos) ÷ receitas, com série histórica. | Para um investidor de longo prazo é o número que mais pesa e o mais fácil de mudar [S37]. Alimenta as ideias 1, 4 e 6. | Prompts de lifestyle creep e data FI; "savings rate" [S16][S37] | P |
| 3 | **Reserva de emergência e runway em meses.** (saldo em conta + ativos do bogle marcados como reserva) ÷ média de gasto **essencial** de 6 a 12 meses. Alerta no `bogle check` abaixo de N meses. | Junta os dois lados: os ativos do bogle (Tesouro Selic, CDB de liquidez) e o gasto real. Pede o atributo de natureza do grupo (#116). | Widget Cash Runway; Radar "runway below six months"; "Build a cash buffer"; "Audit idle cash" [S6][S8][S47] | M |
| 4 | **Número e data de independência financeira pelo gasto real.** FI = (gasto anual − renda garantida) ÷ taxa de saque, comparado ao patrimônio do bogle. Pode alimentar `bogle withdraw` e `retirement_age` com o gasto observado em vez de um valor digitado. | O bogle já tem glide path e saque na aposentadoria, mas sem o gasto real o alvo é chute. | FI number [S40]; "Monitor my financial-independence date" [S47] | M |
| 5 | **Recorrências e assinaturas.** Detecção determinística: mesma contraparte ou descrição normalizada, valor em ±10%, intervalo de cerca de 30 ou 365 dias, 3 ou mais ocorrências. Custo anualizado, alerta de aumento de preço, assinatura nova e cobrança duplicada. | É o único uso de gastos que os depoimentos elogiam com valor concreto ("over $2000/year"). Sai de dados que já existem. | Aba Subscriptions; "Watch my subscriptions"; "Find recurring expenses"; auditoria anual de despesas com revisão trimestral das recorrências [S5][S47][S49][S38] | M |
| 6 | **Anomalias no `bogle check`.** Estabelecimento novo acima de X, despesa única acima de X, grupo acima do normal (ex.: acima da média + 2 desvios). Com deduplicação. | Reaproveita o `bogle check` e a notificação do macOS. Dá valor sem exigir orçamento. | "Check my spending every week"; "expense above $2,000" [S8][S47] | P/M |
| 7 | **Calendário de compromissos de 90 dias.** Faturas projetadas + parcelas + despesas irregulares cadastradas (IPVA, IPTU, seguro) contra o saldo em conta. | Antecipa o mês apertado, que é o depoimento das "upcoming large bills". Estende a #118. | "Upcoming obligations"; calendário de 90 dias; cobertura = caixa ÷ obrigações dos próximos 12 meses [S37][S44][S41] | M |
| 8 | **Provisão de despesas irregulares.** Total anual ÷ 12 como gasto "virtual" mensal no orçamento e no runway. | Sem isso, o mês do IPVA parece descontrole e o orçamento fixo da #119 estoura todo ano no mesmo mês. | [S39] | P |
| 9 | **Lifestyle creep.** 3 meses móveis contra o mesmo período do ano anterior, por grupo, separando pontual de recorrente, com efeito na taxa de poupança. | Gasto que cresce devagar é o que mais atrasa a independência financeira. Precisa de mais de 12 meses de histórico, que a Pluggy ainda não dá. Entra quando o banco tiver acumulado dados. | "Detect lifestyle creep" [S47] | M |
| 10 | **Fechamento mensal (snapshot).** Relatório do mês fechado com patrimônio, fluxo, taxa de poupança, maiores variações por grupo e estabelecimento, e aporte feito. Gravado no banco ou exportado em HTML/CSV. | Revisão mensal é o outro hábito elogiado (relatório revisado com a esposa). Gravar o snapshot também protege contra correções retroativas que mudam o passado. | "Personal financial statement" mensal; "monthly report with a spreadsheet"; cadência semanal, mensal, trimestral e anual de revisão [S10][S47][S43] | M |
| 11 | **Atribuição da variação do patrimônio.** Δpatrimônio = aportes (da sobra) + retorno de mercado (TWR já existe) − saques, mês a mês. | Responde "fiquei mais rico porque economizei ou porque o mercado subiu?". | "Explain the last 90 days" [S47] | M |
| 12 | **Importação de extrato (CSV/OFX do Nubank) como alternativa à Pluggy**, com deduplicação. | O acesso depois do trial ainda é dúvida aberta da #112. Sem plano B, o módulo inteiro para. | Importação CSV/PDF e o problema de duplicatas [S5][S11] | M |
| 13 | **Comprometimento de renda com parcelas.** Parcelas futuras ÷ receita média, por mês. | É um caso brasileiro que o Silvia não cobre. Sai quase de graça da #118. | — (lacuna do Silvia) | P |
| 14 | **Metas com reserva de dinheiro (earmarking).** Uma meta aponta para ativos específicos do bogle, não para o patrimônio total. | Evita a falha vista no Silvia: meta "concluída" sem dinheiro reservado (2.8). Útil para metas de prazo curto (carro, viagem), que não cabem no glide path. | Goals [S7] (como contraexemplo) | M/G |

As ideias 1, 2 e 3 dependem de a #115 gravar o motivo `investimento` (aplicação/resgate) separado de `transferência`, e de a #116 ter o atributo de natureza do grupo. Convém decidir esses dois pontos antes de implementar as sub-issues.

---

## 7. Fontes

Acessadas em 27/11/2026.

**Documentação oficial (docs.cfosilvia.com)**

- [S5] Transactions: https://docs.cfosilvia.com/product-guide/portfolio/transactions. Screenshots: `transactions__general-access.png`, `qa-2026-11-25/transactions--rules--4k.png`, `recovery/transactions--categorization--change-category-and-apply-to-similar--closeup.png`, `transactions__actions-general-access.png`, `transactions__import.png`, servidos por mintcdn.com.
- [S6] Dashboard: https://docs.cfosilvia.com/product-guide/dashboard (screenshot `dashboard/2026-11-25--dashboard--cash-flow.png`)
- [S7] Goals: https://docs.cfosilvia.com/product-guide/portfolio/goals (screenshots `goals__create.png`, `goals__general-access.png`)
- [S8] Radar: https://docs.cfosilvia.com/product-guide/radar (screenshot `radar__general-access.png`)
- [S9] Ask Silvia: https://docs.cfosilvia.com/product-guide/ask-silvia
- [S10] Settings: https://docs.cfosilvia.com/account/settings
- [S11] Troubleshooting: https://docs.cfosilvia.com/help/troubleshooting
- [S12] FAQ (docs): https://docs.cfosilvia.com/help/faq
- [S13] Subscription & billing: https://docs.cfosilvia.com/account/subscriptions
- [S14] Import Holdings: https://docs.cfosilvia.com/product-guide/assets/import-holdings-csv
- [S15] QuickBooks: https://docs.cfosilvia.com/product-guide/assets/quickbooks
- [S61] Índice: https://docs.cfosilvia.com/llms.txt

**Site oficial (www.cfosilvia.com)**

- [S1] Home: https://www.cfosilvia.com/
- [S2] Pricing: https://www.cfosilvia.com/pricing
- [S3] FAQ: https://www.cfosilvia.com/faq
- [S4] About: https://www.cfosilvia.com/about
- [S16] AI Transparency: https://www.cfosilvia.com/ai-transparency
- [S17] Terms of Service: https://www.cfosilvia.com/terms-of-service
- [S18] Privacy Policy: https://www.cfosilvia.com/privacy-policy
- [S19] Security: https://www.cfosilvia.com/help/security
- [S20] Connect Chase: https://www.cfosilvia.com/help/connect-chase
- [S21] MCP setup: https://www.cfosilvia.com/mcp/setup
- [S48] Budget Calculator: https://www.cfosilvia.com/financial-calculators/budget
- [S49] Testimonials: https://www.cfosilvia.com/testimonials
- [S47] Prompt library, prompts citados:
  - https://www.cfosilvia.com/prompts/check-my-spending-every-week
  - https://www.cfosilvia.com/prompts/understand-where-the-money-went
  - https://www.cfosilvia.com/prompts/detect-lifestyle-creep
  - https://www.cfosilvia.com/prompts/find-recurring-expenses-worth-reviewing
  - https://www.cfosilvia.com/prompts/watch-my-subscriptions
  - https://www.cfosilvia.com/prompts/turn-my-spending-into-a-budget-spreadsheet
  - https://www.cfosilvia.com/prompts/set-a-safe-spending-number
  - https://www.cfosilvia.com/prompts/build-a-cash-buffer
  - https://www.cfosilvia.com/prompts/audit-idle-cash
  - https://www.cfosilvia.com/prompts/use-excess-cash-deliberately
  - https://www.cfosilvia.com/prompts/explain-the-last-90-days
  - https://www.cfosilvia.com/prompts/monitor-financial-independence-date
  - https://www.cfosilvia.com/prompts/send-me-a-monthly-report-with-a-spreadsheet
  - https://www.cfosilvia.com/prompts/build-my-financial-command-center
  - https://www.cfosilvia.com/prompts/find-one-thing-i-havent-noticed

**Blog oficial**

- [S22] https://www.cfosilvia.com/blog/ai-product/silvia-mcp
- [S23] https://www.cfosilvia.com/blog/ai-product/silvia-radar
- [S24] https://www.cfosilvia.com/blog/ai-product/invite-your-spouse-to-silvia
- [S25] https://www.cfosilvia.com/blog/financial-technology/silvia-multi-portfolio
- [S26] https://www.cfosilvia.com/blog/financial-technology/silvia-multi-currency
- [S27] https://www.cfosilvia.com/blog/ai-product/how-silvia-protects-your-financial-data
- [S28] https://www.cfosilvia.com/blog/financial-technology/cfo-silvia-vs-ynab
- [S29] https://www.cfosilvia.com/blog/financial-technology/cfo-silvia-vs-rocket-money
- [S30] https://www.cfosilvia.com/blog/financial-technology/silvia-vs-monarch-money
- [S31] https://www.cfosilvia.com/blog/financial-technology/cfo-silvia-vs-quicken
- [S32] https://www.cfosilvia.com/blog/financial-technology/mint-alternatives
- [S33] https://www.cfosilvia.com/blog/financial-technology/best-ai-financial-assistants
- [S34] https://www.cfosilvia.com/blog/financial-technology/best-finance-apps
- [S35] https://www.cfosilvia.com/blog/financial-technology/cfo-silvia-vs-origin
- [S36] https://www.cfosilvia.com/blog/financial-technology/how-ai-uses-connected-financial-data
- [S37] https://www.cfosilvia.com/blog/financial-technology/personal-financial-dashboard
- [S38] https://www.cfosilvia.com/blog/finance/how-to-run-an-expense-audit
- [S39] https://www.cfosilvia.com/blog/finance/how-to-manage-household-finances
- [S40] https://www.cfosilvia.com/blog/finance/how-to-calculate-your-financial-independence-number
- [S41] https://www.cfosilvia.com/blog/finance/how-high-net-worth-families-monitor-liquidity
- [S42] https://www.cfosilvia.com/blog/financial-technology/which-portfolio-alerts-are-actually-useful
- [S43] https://www.cfosilvia.com/blog/financial-technology/how-to-build-a-personal-financial-operating-system
- [S44] https://www.cfosilvia.com/blog/finance/building-a-clearer-financial-system
- [S46] https://www.cfosilvia.com/blog/ai-product/silvia-reborn

**Terceiros**

- [S50] Product Hunt, avaliações: https://www.producthunt.com/products/my-cfo-silvia/reviews
- [S51] Tweed, "CFO Silvia Review 2026" (atualizado em 07/11/2026): https://usetweed.com/blog/cfo-silvia-review/
- [S52] Tweed, "CFO Silvia vs Monarch Money" (26/10/2026): https://usetweed.com/blog/cfo-silvia-vs-monarch-money/
- [S53] Amjid Ali, "CFO Silvia Review" (21/10/2026): https://amjid.au/insights/is-silvia-ai-safe-honest-review/
- [S54] ScoutForge (01/09/2026): https://scoutforge.net/apps/silvia
- [S55] InvestmentNews (fev/2026): https://www.investmentnews.com/transformation/anthony-pompliano-takes-ai-portfolio-analysis-public-with-silvia/265202
- [S56] The Pomp Letter, "Artificial Intelligence Is Eating Personal Finance" (07/11/2025): https://pomp.substack.com/p/artificial-intelligence-is-eating
- [S57] Comunicado da troca de ticker para SVIA (22/09/2026), via Yahoo Finance: https://finance.yahoo.com/markets/stocks/articles/silvia-inc-begins-trading-nasdaq-110000787.html
- [S58] G2: https://www.g2.com/products/cfo-silvia/reviews
- [S59] Comunicado de corte de custos (20/10/2026): https://stockhouse.com/news/press-releases/2026/10/20/silvia-inc-announces-aggressive-cost-cutting-initiative-expected-to-deliver-more. Não abriu (HTTP 403); conteúdo só pelo resumo da busca.
- [S60] Behind the Craft, episódio com Pompliano (18/10/2026, conteúdo pago; só as notas são públicas): https://creatoreconomy.so/p/we-built-an-ai-cfo-to-help-you-grow-net-worth-anthony-pompliano

### O que não foi conferido

- **Reddit (r/CFOSilvia):** o acesso automatizado foi bloqueado e a busca não retornou threads.
- **Lojas de apps:** não há listagem do produto. A API de busca do iTunes não achou nada com os termos "cfo silvia", "silvia ai cfo", "silvia ai", "silvia inc", "silvia cfo", "silvia finance" e "silvia net worth". A busca na web também não achou nada no Google Play.
- **YouTube:** não achei demonstração com transcrição. O episódio do Behind the Craft é pago.
- **Comportamento real do produto:** nenhuma conta foi criada. Tudo o que está aqui vem de documentação, screenshots de demonstração e textos públicos. Não foi possível testar precisão da categorização, detecção de transferências ou recorrências.
- O **conteúdo dos 22 comentários** do post do fundador no X (via Digg) não aparece na página.
