# Anotadin: benchmark de produto para o épico de gastos (#112)

Coleta em 27/11/2026, para alimentar a #112 e as sub-issues #114 a #120. Fontes numeradas como [S1], [S2] etc. e listadas na seção 7. As respostas ao post do fundador aparecem como [R1], [R2] etc.

Marcação de evidência:

- **[F]** fato documentado em página pública do produto (landing, script da landing, política de privacidade, posts do fundador).
- **[P]** fato observado no protótipo mostrado em vídeo e print pelo fundador em 16 e 17/11/2026. É o app anterior ao rebatismo ("Skunk") e pode mudar até o lançamento.
- **[I]** inferência minha a partir das evidências acima.

## 0. Limites da evidência

O Anotadin **ainda não foi lançado**. Em 27/11/2026 existe só uma landing com lista de espera [S1][S3]. O link "Já tenho convite" fica escondido porque, segundo o próprio arquivo de configuração, "o app ainda não está no ar" [S3]. Em consequência:

- Não há app na App Store nem no Google Play [S15][S16], nem página no Reclame Aqui (a busca devolveu 403 e a busca na web não achou nada) [S17]. Também não há central de ajuda, termos de uso, blog, preços ou documentação de API. O sitemap tem só a home e a privacidade [S5].
- Não existem avaliações de usuários. A voz do usuário vem das 412 respostas ao post viral do fundador [S12]. São pessoas reagindo a um vídeo de 43 s, não clientes usando o produto. A seção 4 trata isso como sinal de demanda, não como avaliação.
- O protótipo público ("skunk-public.vercel.app") foi removido: em 27/11 devolve 404 `DEPLOYMENT_NOT_FOUND` [S18]. O que se sabe do app real vem dos frames dos dois vídeos e dos dois prints do post [S8][S9][S10].
- Existe um homônimo sem relação: "ANOTADIN", app de notas open source no GitHub [S19]. Foi descartado.

Na prática, o benchmark vale mais pelos **conceitos de análise** (quanto ainda dá para gastar, ritmo do mês, separação entre fixos e dia a dia, limites só onde dói, fatura aberta) do que pelo tratamento de casos difíceis. Os casos difíceis quase não aparecem, porque o app é de lançamento manual.

## 1. Visão geral do produto

| Aspecto | O que se sabe | Fonte |
|---|---|---|
| O que é | "Controle de gastos que reage a cada compra". Lançamento manual em dois toques, um número principal ("quanto ainda dá pra gastar no mês" e por dia) e um mascote porquinho que comenta cada gasto. | [F] [S1] |
| O que não é | "Não guarda, não paga e não movimenta dinheiro." Não é banco nem conta digital. | [F] [S1] |
| Público | Não declarado. Tom de rede social, gírias, humor. Slogan: "pra quem esquece o que gastou ontem". O questionário da lista pergunta faixa etária (de "menos de 18" a "45 ou mais") e como a pessoa controla gastos hoje (planilha, app do banco, outro app, bloco de notas, nada). | [F] [S1][S2] |
| Tese | Planilha "funciona até o dia em que você esquece de abrir"; app do banco "mostra o que já foi, de um banco só. Quanto ainda dá pra gastar, ele não diz". | [F] [S1] |
| Modelo de negócio | Freemium. **Grátis** "pra sempre": anotar, saber quanto ainda dá, mascote com voz. **Pro**: "os agentes" (voz, foto de cupom, print de Pix, WhatsApp, análise do mês, conversa com Claude e ChatGPT). Preço não divulgado. O questionário pergunta "Quanto você pagaria por mês no Pro?". Sem anúncios e sem venda de dados. | [F] [S1][S2] |
| Plataformas | App web instalável (PWA) no iPhone, no Android e no computador. Lojas "vêm depois". WhatsApp só no Pro. O protótipo já era PWA, com tela própria de instruções "Instale no celular". | [F] [S1]; [P] [S9] |
| Infra | Supabase (servidores em São Paulo) e Vercel Web Analytics, citados na política de privacidade. | [F] [S4] |
| Agregador / Open Finance | **Nenhum agregador citado.** FAQ: "Conectar o banco pelo Open Finance vai ser opcional, para quem quiser". O protótipo é 100% manual: a "conta" é escolhida à mão no lançamento ("Nubank" ou "Cartão Nubank"). Usuários sugeriram Pluggy e Open Finance nas respostas [R5][R6]. | [F] [S1]; [P] [S8] |
| Origem e linha do tempo | 16/11/2026: protótipo "Skunk" gravado (o vídeo mostra 16:28, com fatura "fecha 21 de nov (em 5 dias)"). 17/11: post "Larguei as planilhas e criei meu próprio organizador financeiro com mascote que me julga". 18/11: anotadin.com só com "Em breve" [S6]. 19/11: fundador anuncia "equipe e identidade novas" e a lista de espera [S11]. Política de privacidade datada de 17/11 [S4]. | [F] |
| Fundador | Perfil @caiaop_: "Product Designer & Product Manager", São Paulo [S13]. | [F] |
| Tração | Post: cerca de 173 mil visualizações, 5,8 mil curtidas, 2,7 mil salvamentos, 548 respostas e 30 citações (em 27/11). A landing cita "162 mil visualizações do mascote no X". Instagram @anotadin.app com 17 seguidores e nenhum post visível [S14]. Tamanho da lista de espera não verificado: a landing só mostra o contador a partir de 100 inscritos [S3]. | [F] [S7][S1] |
| Crescimento | Lista com posição e indicação: "Cada amigo que entrar pelo seu link te faz subir na lista". | [F] [S1][S2] |

## 2. Inventário de funcionalidades de gestão de gastos

Fora do interesse do bogle e só listados (seção 2.15): voz, foto, WhatsApp, agente de IA, conexão com Claude/ChatGPT.

### 2.1 Lançamento manual em dois toques

- **Como funciona.** Folha "Novo lançamento" com alternância Gasto/Receita, teclado numérico próprio (com "00" e atalhos +10, +20, +50, +100), carrossel de categorias com ícones, escolha de conta ("Nubank" ou "Cartão Nubank") e uma seção recolhida "Data, nota e mais" (nota, com o exemplo "Uber pro metrô", e data). Botão "Lançar gasto". [P] [S8]
- **Prévia de impacto antes de salvar.** Sem categoria escolhida, o subtítulo mostra "Pode gastar no mês: R$ 984". Ao escolher Mercado com R$ 100 digitados, passa a mostrar "Mercado e alimentação: **sobram R$ 300** de R$ 400". [P] [S8]
- **Categoria sugerida.** "A categoria já vem sugerida. Salvou, foi." e "nada de categoria pra configurar". [F] [S1] Não há evidência de como a sugestão funciona.

### 2.2 Contas e cartões

- O lançamento pede a conta. No protótipo havia uma conta corrente e um cartão do mesmo banco. [P] [S8]
- A landing promete "Todos os cartões num lugar". [F] [S1]
- [I] A conta escolhida decide se o gasto vai para a fatura: é o que permite o card "Fatura aberta" (2.8).

### 2.3 Categorias padrão

Categorias vistas no protótipo, com limite de exemplo [P] [S8]:

| Categoria | Limite no exemplo | Observação |
|---|---|---|
| Mercado e alimentação | R$ 400 | |
| Restaurantes e delivery | R$ 200 | separada de mercado |
| Transporte | R$ 200 | |
| Lazer, compras e pessoal | R$ 100 | três temas num balde só |
| Saúde e imprevistos | R$ 84 | "imprevistos" junto com saúde |
| Moradia | sem limite | categoria de fixos |
| Contas e celular | sem limite | categoria de fixos |
| Assinaturas | sem limite | categoria de fixos |

A lista continua abaixo de "Assinaturas" no vídeo e não dá para ler o resto. A landing cita também "Delivery", "Bar", "Compras", "Comida" e "Outro" nos exemplos [F] [S1]. Não se sabe se são as mesmas categorias com outro nome.

### 2.4 "Pode gastar": um número, não dez

- **Fórmula declarada:** "Teto do mês, menos o que já foi, menos os fixos que faltam. Dividido pelos dias que restam." [F] [S1]
- **Na tela inicial do protótipo:** anel de progresso com o mascote, "Pode gastar R$ 984,06", "de R$ 984 · 15 dias" e o selo "No ritmo". Em 16/11, "15 dias" inclui o dia corrente (16 a 30). [P] [S8]
- **Por dia:** "R$ 51 por dia · faltam 12 dias" e "Pode gastar hoje R$ 51". [F] [S1]
- **Teto do mês = soma dos limites por categoria.** Na tela Planejar: "Limite do mês R$ 984,06, soma dos limites por categoria". As categorias de fixos ficam "sem limite". [P] [S8]
- **"Fixos não entram aqui. O limite do mês vale pra merc…"** (texto cortado) e o balão "R$ 984 pro dia a dia. Isso é um limite, não uma meta." [P] [S8]
- [I] Há uma tensão entre a fórmula da landing (teto menos os fixos que faltam) e o protótipo (teto do dia a dia sem fixos). A leitura mais provável: o protótipo tinha um teto só do dia a dia e a landing descreve um teto total que desconta os fixos ainda não pagos. Com os dados públicos não dá para decidir.
- [I] O total de R$ 984,06, com centavos, ao lado de "Saúde e imprevistos R$ 84" sugere que um dos limites é calculado como resto de um teto, não digitado. Confiança baixa.

### 2.5 Ritmo do mês

- "Compara o que você gastou com quanto do mês já passou." Exemplo: "Já foi 40% do teto, em 33% do mês", com o selo "acelerado". [F] [S1]
- Estados vistos: "no ritmo" e "acelerado". [F] [S1]; [P] [S8]
- [I] O cálculo é linear (fração do teto contra fração dos dias). Como os fixos ficam fora do teto, o viés de começo de mês (aluguel no dia 5) não distorce o ritmo.

### 2.6 Limites por categoria ("Limite só onde dói")

- "Delivery, bar, compras. **Aviso em 80% e em 100%.**" [F] [S1]
- Cada categoria com limite mostra "R$ 0 de R$ 400 este mês" e uma barra. A home tem o bloco "Limites do mês" com o link "Ajustar". [P] [S8]
- Categoria sem limite não entra no teto e não gera aviso. [P] [S8]
- Faixas do mascote na demo interativa da landing: 0% "tudo certo"; abaixo de 50% "no ritmo"; abaixo de 85% "atenção"; abaixo de 100% "quase lá"; 100% ou mais "estourou". O mascote "fica mais rosa" conforme enche. [F] [S2] É código de demonstração e as faixas podem diferir do app.

### 2.7 "O mês em uma conta" e Fixos

- Bloco da tela inicial [P] [S8]:
  - **Entra**: "+R$ 0,00 · lance seu salário em Fixos";
  - **Fixos**: "−R$ 0,00 · tudo pago";
  - **Dia a dia**: "−R$ 0,00 · limite R$ 984";
  - **Sobra**: "R$ 0,00 · contando o que ainda vai entrar e sair".
- **Fixos** (tela Planejar → "Fixos e salário"): resumo "Entra por mês" / "Sai por mês" e cadastro de fixos com dia ("Aluguel todo dia 5 R$ 1.800", "Spotify todo dia 12 R$ 21,90"). Texto de estado vazio: "Comece pelo salário e pelo aluguel. Eles aparecem todo mês no Início pra você **marcar como pago**." [P] [S8]; [F] [S1]
- [I] Fixo é um item recorrente com estado por mês (pendente ou pago). "Fixos que faltam" são os pendentes, e é isso que a fórmula do 2.4 desconta. A receita principal (salário) é modelada como fixo de entrada.
- [I] "Sobra" parece ser a projeção do saldo do mês (entradas previstas menos fixos menos dia a dia). Não dá para saber se o dia a dia entra pelo gasto realizado ou pelo limite reservado.

### 2.8 Fatura aberta e parcelas

- Card "Fatura aberta" na tela inicial: "Cartão Nubank · R$ 0,00 · Fecha 21 de nov (em 5 dias) · vence 28 de nov · 0 compras". [P] [S8]
- "Comprou em 10x? Cada parcela cai na fatura certa." Exemplo: "Dezembro · fatura fecha dia 28 · R$ 1.240" e "Tênis · parcela 3/10 · R$ 39,90". [F] [S1]
- [I] Como tudo é manual, a pessoa deve informar fechamento e vencimento do cartão e o número de parcelas da compra. Não há evidência do formulário.
- Não há evidência de pagamento parcial, rotativo, pagamento mínimo, antecipação de parcelas ou fatura de mais de um cartão somada.

### 2.9 Transações e navegação

- Tela "Transações": busca "por nota, categoria ou conta", filtros "Gastos" e "Receitas", estado vazio "Nenhum lançamento em novembro. Toque em + pra anotar." [P] [S8]
- Todas as telas têm seletor de mês (‹ Novembro ›) e um botão de ajustes. Barra inferior: Início, Transações, +, Planejar, Ajustes. [P] [S8]
- Desfazer: "mostra tudo o que fez, com desfazer", citado no bloco do Pro. [F] [S1]

### 2.10 Lembretes e avisos

- "Um lembrete para anotar, na hora que você escolher." [F] [S1]
- "Aviso antes da fatura vencer." Exemplo de notificação: "A fatura vence amanhã · R$ 212". [F] [S1]
- "Um resumo da semana toda segunda." Exemplo: "Sua semana: R$ 312 · Comida quase no limite". [F] [S1]
- Aviso de inatividade: "Nada anotado hoje?" e, no card ao vivo, "Nada anotado há 3 dias". [F] [S1][S2]
- A seção de lembretes não tem selo "Pro"; as seções de voz, WhatsApp e agente têm [F] [S1]. A tabela de planos não cita lembretes, então o plano a que pertencem é incerto.

### 2.11 Reações do mascote (taxonomia de eventos)

O mascote em si não interessa ao bogle. Interessa a **lista de eventos** que o app considera dignos de aviso:

| Estado | Gatilho | Exemplo de fala | Fonte |
|---|---|---|---|
| Triste | "O mês passou do limite" | "Estourou R$ 80. Vou fingir que não vi." | [P] [S10] |
| Bravo | "Um gasto estourou a categoria" | "Mercado ESTOUROU. Eu avisei." | [P] [S10] |
| Surpreso | gasto grande de uma vez; a condição aparece cortada no print ("… ou …% do limite") | "R$ 450?! De uma vez só?" | [P] [S10] |
| Bravo | gasto de madrugada | "Uber, hoje, 2h da manhã · Madrugada de novo? Anotei." | [F] [S1][S2] |
| Amor | gasto dentro do limite | "Mercado · dentro do limite · Gastou o combinado. Orgulho." | [F] [S2] |
| Sono | nada anotado há dias | "Ou não gastou, ou tá escondendo." | [F] [S2] |
| Alerta | categoria em 85% | "Mercado já tá em 85%. Eu vi, viu." | [F] [S1] |
| Início de mês | limite cheio | "Mês novo, limite cheio: R$ 984. Respira antes de abrir o iFood." | [P] [S8] |

Regra de tom declarada: "ele comenta o gasto, nunca quem gastou". [F] [S1]

### 2.12 Quanto custa em horas

- A pessoa informa "Ganho por mês" e "Horas por mês"; o app divide e mostra cada gasto também em tempo ("R$ 38 parece pouco. Duas horas e meia, nem tanto"). "No app, todo gasto já aparece assim." [F] [S1]
- A calculadora da landing divide a renda pelas horas e formata em h/min, com botão para compartilhar o resultado. [F] [S2]

### 2.13 Planejar

Tela que junta "Fixos e salário" (entra e sai por mês, cadastro) e "Limites do dia a dia" (limite do mês e limite por categoria). É o lugar da configuração; a home é só leitura e atalhos (Gasto, Receita, Fixos, Limites). [P] [S8]

### 2.14 Privacidade

"Sem anúncios, sem venda de dados, e seus gastos não são usados pra treinar IA." A conexão com IA externa exige permissão. [F] [S1][S4]

### 2.15 Plano Pro (fora do interesse, só para registro)

Lançamento por áudio, por foto de cupom fiscal e por print de Pix; WhatsApp ("gastei 89 na shein", com pergunta de volta quando falta categoria); perguntas sobre os gastos ("posso gastar 200 num tênis?", com resposta "Cabe. Mas aperta: depois sobram R$ 34 por dia"); análise do mês; conexão com Claude e ChatGPT. [F] [S1]

O que vale extrair daqui sem IA: o cálculo "cabe?" (2.4 aplicado a uma compra hipotética) e as consultas por estabelecimento com contagem ("R$ 186,40 em 9 corridas").

### 2.16 O que não foi encontrado

Sem evidência pública de: metas de economia (aparecem só como opção do questionário: "Metas pra guardar") [S2]; reserva de emergência; investimentos ou patrimônio; relatórios históricos ou tendência de vários meses além do seletor de mês; exportação ou importação (CSV, OFX, PDF de fatura); importação via Open Finance funcionando; tags; divisão de despesas e reembolso; orçamento que acumula sobra para o mês seguinte; multiusuário ou família; múltiplas moedas.

## 3. Regras de negócio e casos difíceis

| Caso | O que o Anotadin faz | Evidência | Leitura para o bogle |
|---|---|---|---|
| Mês de referência de compra no cartão | [I] A compra conta no limite do mês em que foi lançada: a prévia "sobram R$ 300 de R$ 400" aparece no ato, com a conta "Cartão Nubank". A fatura é só onde a compra cai. | [P] [S8] | Apoia a proposta da #112: data da transação nas análises (15.4) e visão por fatura na 15.5. |
| Parcelas | "Cada parcela cai na fatura certa"; o item "Tênis parcela 3/10" aparece na fatura de dezembro. [I] A parcela conta no mês da fatura, não o total no mês da compra. | [F] [S1] | Com a Pluggy, cada parcela futura já vem como transação `PENDING` com data própria. Pela data da transação, uma compra em 10x se espalha sozinha por 10 meses. |
| Pagamento de fatura | Não documentado. No modelo manual o gasto é a compra. [I] Se alguém cadastrar a fatura como fixo, conta em dobro, e não há sinal de proteção contra isso. | ausência | Reforça a regra da #115: o gasto é a compra, o pagamento da fatura é liquidação e sai dos dois lados. |
| Transferência entre contas próprias | Não documentado. No manual, a pessoa simplesmente não lança. | ausência | Nada a copiar. A #115 resolve isso com dados. |
| Pix a terceiros | "Print do Pix" vira lançamento de gasto (Pro). [I] Pix pago a pessoa é tratado como gasto comum. | [F] [S1] | Cuidado na #115: excluir a categoria "Transfers" inteira tiraria das análises o Pix a terceiros (aluguel pago a pessoa física, diarista, racha de conta). Só transferência para conta própria deve sair. |
| Estorno | Não documentado (não há fluxo de estorno nem de reembolso). | ausência | A #115 já trata. Ver riscos na seção 5. |
| Recorrências e assinaturas | "Fixos": cadastro único, entram todo mês, marcados como pagos. A landing usa "A assinatura que você esqueceu R$ 44,90" como exemplo de custo em horas. | [F] [S1]; [P] [S8] | A detecção automática de fixos é possível com dados da Pluggy (mesmo CNPJ ou descrição, valor parecido, periodicidade mensal). Ver ideias 3 e 4. |
| Receitas | Alternância Gasto/Receita; salário como fixo de entrada ("lance seu salário em Fixos"). Receitas ficam fora do "Pode gastar". | [P] [S8] | Coerente com a #117 (receitas à parte). Receita recorrente é o que permite projetar a "Sobra". |
| Fixos contra dia a dia | Separação explícita: limites e ritmo valem só para o dia a dia; fixos têm tratamento próprio. | [P] [S8] | É o ponto mais aproveitável. Ver #116 e #119. |
| Dias restantes | Conta o dia corrente (16/11 → 15 dias). | [P] [S8] | Detalhe de cálculo para o "por dia". |
| Gasto único grande | Gatilho próprio ("De uma vez só?"), com limiar absoluto ou percentual do limite (texto cortado). | [P] [S10] | Alerta barato de implementar. |
| Estouro | Dois eventos distintos: a categoria estourou e o mês estourou. | [P] [S10] | Separar os dois na #119. |
| Inatividade | Lembrete diário para anotar; "nada anotado há 3 dias". | [F] [S1][S2] | Análogo no bogle: sync parado ou consentimento perto de vencer. |
| Metas e reserva | Inexistentes no produto; só aparecem como opção do questionário. | [F] [S2] | Espaço livre para o bogle, que tem a carteira. |
| Erro de lançamento | "Desfazer" (Pro). | [F] [S1] | No bogle, o equivalente é a correção manual com precedência sobre a regra (#116). |
| Limite contra meta | "Isso é um limite, não uma meta." | [P] [S8] | Texto curto que evita ler o orçamento como alvo a atingir. |

## 4. O que os usuários pedem e elogiam

### 4.1 Fonte e método

Não há avaliações em loja nem no Reclame Aqui (seção 0). Foram coletadas as respostas ao post de 17/11 pela API pública do FxTwitter: 359 respostas de primeiro nível mais 53 respostas aninhadas nos fios com mais conversa, 412 no total sem contar o autor (o post mostra 548; o restante não veio pela API) [S12]. A classificação é por palavras-chave mais leitura manual; uma resposta pode ter mais de um tema. Os números são aproximados.

### 4.2 Contagem por tema

| Tema | Respostas (aprox.) | Exemplo |
|---|---|---|
| Pedido de acesso ("manda", "libera", "quero", "cadê o link") | ~300 de 412 | as duas respostas mais curtidas: "tava precisando de uma parada desse URGENTE" (82) e "Lança pra todo mundo, covarde" (81) [R1][R2] |
| Elogio ao visual, ao mascote ou à ideia | ~60 | "Muito fofo. Eu fiz um mas o meu ficou com uma cara mais de dashboard do que interativo como o seu" [R13] |
| **Quer captura automática / Open Finance** | **8** (inclui o pedido de funcionalidade mais curtido, com 32 curtidas) | "queria mt um desse mas q registrasse automaticamente as compras c meu cartao de credito, odeio ter q ficar atualizando manualmente" [R3]; "Integra logo isso com open finance, fazer lance manual é horrível de chato" [R4]; "Conecta isso num pluggy.ai da vida" [R5]; "se tiver open finance você matou se não, o pierre segue sendo o maioral" [R6] |
| Plataforma (loja, PWA, iPhone, Android) | ~12 | "pretende lançar em loja de app?" (39 curtidas) [R7] |
| Open source, repositório, self-hosted | ~8 | "queria achar um parecido, só que selfhosted" [R8] |
| Já fez o próprio app de finanças | ~10 | leitura de notificações do celular para só confirmar o gasto [R9]; mandar print dos gastos recentes e reconhecer as compras [R10]; "só precisa jogar o pdf da fatura", que outro usuário rebate com "tbm ta ruim" [R11] |
| Outros canais de captura (notificação, print, PDF, atalho do iOS, WhatsApp) | ~5 | "integração com o Shortcuts do iOS", dois toques que abrem um formulário [R12] |
| Preço ("é pago?", "Pro acessível") | ~5 | "coloca o preço do pro em um valor acessivel"; o fundador responde que fará "o mais acessível possível" [R14] |
| Desconfiança com dados | 1 | sobre outro app anunciado no YouTube: "tem mta cara de que vai roubar meus dados" [R15] |
| Crítica direta ao produto | 1 a 2 | "Oba mais um saas de finanças" [R16] |

### 4.3 Leitura

- **A dor mais citada é o lançamento manual.** O pedido de funcionalidade mais curtido é captura automática do cartão (32 curtidas; a única resposta de conteúdo com mais curtidas pergunta se o app vai para as lojas). Quem já construiu o próprio app tenta contornar a digitação (notificação, print, PDF da fatura) e ainda acha ruim [R3][R9][R10][R11]. O bogle começa pelo lado certo: dados da Pluggy, zero digitação.
- **O que atraiu foi o "quanto ainda dá" com reação imediata**, não relatórios. O fundador resume a tese: app do banco mostra o passado; o Anotadin diz quanto ainda dá e avisa antes [S1].
- **Concorrente citado espontaneamente: Pierre**, lembrado pelo Open Finance, com a crítica "o pierre é muito burro" [R6][R17]. É uma pista de que categorização ruim incomoda até quem tem captura automática. Não foi investigado aqui.
- **Prioridades que o próprio Anotadin pergunta na lista**: anotar rápido, saber quanto ainda dá, controlar cartão e parcelas, lembrete de contas, metas pra guardar, voz ou foto, WhatsApp, conectar com o banco [S2]. As respostas do questionário não são públicas.
- Viés: quem responde a um post viral pede acesso, não avalia. A contagem mede curiosidade e o que a pessoa sente falta só de olhar o vídeo.

## 5. Mapeamento contra as sub-issues #114 a #120

### #114 (15.1) Cliente Pluggy, schema e sync automático

- **Já coberto.** O Anotadin não tem sync; nada a copiar. A demanda por captura automática (seção 4) confirma a escolha da #112.
- **Sugere acrescentar.**
  - Mostrar a **frescura dos dados** junto ao número principal ("dados até 26/11"). O "pode gastar" e o ritmo dependem disso, e a Pluggy atualiza a cada 24 h.
  - Equivalente do "nada anotado há 3 dias": aviso quando o último sync bem-sucedido tem mais de N dias, ou quando o `consentExpiresAt` do item está a menos de 30 dias. Cabe no `bogle check`.
  - Guardar o status `PENDING`/`POSTED` de forma que a 15.5 consiga mostrar "fatura aberta: N compras".
- **Riscos.** Dado atrasado parece mês "no ritmo": falso conforto. Sem a data de corte visível, a análise engana justamente quando o sync falha (e a #114 já decide que falha não bloqueia).

### #115 (15.2) Exclusões: transferências, pagamento de fatura, estorno

- **Já coberto.** O modelo "a compra é o gasto, a fatura é só onde ela cai" (2.8) é o mesmo da #115. Excluir o pagamento da fatura nos dois lados está certo.
- **Sugere mudar ou precisar.**
  - Definir **transferência própria** de forma estrita: categoria "Same person transfer", ou documento da contraparte igual ao CPF do titular, ou movimento espelhado em outra conta conectada (mesmo valor, ±1 dia). Pix a terceiros fica como gasto e passa pelas regras da #116 (por nome ou documento do recebedor). O Anotadin trata print de Pix como gasto (seção 3).
  - **Reembolso recebido** (Pix de um amigo que rachou a conta) como crédito que abate o grupo do gasto, via regra. Hoje cairia como receita e inflaria a renda.
- **Riscos.**
  - Excluir "Transfers" inteira subestima o gasto real. [I] A verificar nos dados do Nubank: quantas transações de "Transfers" vão para terceiros.
  - Estorno abatido no mês em que entra deixa o mês da compra caro e o do estorno barato, e pode zerar ou deixar negativo o grupo do mês seguinte. Alternativa: vincular ao mês da compra quando o estorno casa com uma compra (mesmo valor e estabelecimento). A decisão é da #115; aqui só registro o efeito.
  - Pagamento de fatura feito de outra conta não conectada não casa, e a fatura do cartão fica sem par. Precisa de saída explícita: excluir o lado do cartão mesmo sem par.

### #116 (15.3) Grupos, correção manual e regras

- **Já coberto.** Mapeamento padrão gravado no banco ("nada de categoria pra configurar"), correção manual e regras.
- **Sugere mudar.**
  - **Grupos padrão** inspirados no Anotadin, que separa o que tem comportamento diferente:
    - Mercado e alimentação **separado** de Restaurantes e delivery (delivery é o exemplo-símbolo de "onde dói");
    - **Assinaturas** e **Contas e celular** como grupos próprios, em vez de um "Serviços" genérico;
    - "Saúde e imprevistos" como balde para o não planejado.

    A proposta atual da #116 (Moradia, Alimentação, Transporte, Saúde, Lazer, Compras, Serviços, Outros) junta mercado com delivery.
  - **Atributo de tipo no grupo**: `fixo` ou `dia_a_dia`. É o que o Anotadin usa para tirar fixos do teto e do ritmo ("Fixos não entram aqui"). Sem esse atributo a #119 e o ritmo da #117 ficam distorcidos pelo aluguel.
  - Ao corrigir a categoria, mostrar o efeito no orçamento do grupo de destino ("Lazer: sobram R$ 40 de R$ 100"), como a prévia do lançamento [P] [S8].
- **Riscos.** Grupos demais diluem a leitura; o Anotadin fica em 8 a 10. "Outros" cresce sem ninguém notar (a #116 já prevê o aviso). Regra por descrição pega falso positivo em descrições genéricas de Pix ("PIX ENVIADO") e precisa casar por documento quando houver.

### #117 (15.4) Gastos por grupo e tendência mensal

- **Já coberto.** Total do mês por grupo, comparação com a média, receitas à parte, filtro por conta.
- **Sugere acrescentar.**
  - **"O mês em uma conta"** no topo do `summary`: Entrou, Fixos, Dia a dia, Sobra. Quatro linhas, valor uma vez, compatível com as regras de texto da interface.
  - **Ritmo do mês**: gasto do dia a dia até hoje como % do teto, contra % do mês decorrido. Selo "no ritmo" ou "acelerado".
  - **Estabelecimentos mais frequentes** do mês, com contagem e total ("Uber: 9 compras, R$ 186,40"). Usa o `cnpj`/`businessName` do merchant da Pluggy.
  - **Maior gasto** do mês ou da semana, numa linha.
- **Riscos.**
  - Ritmo linear erra com gasto concentrado: fixos no começo, viagem no meio. Calcular só sobre grupos `dia_a_dia`. Opcionalmente, comparar com a curva acumulada mediana dos meses anteriores no mesmo dia do mês.
  - Com poucos meses de histórico (o Nubank devolveu de jan ou mar/2026 em diante), a "média dos meses anteriores" é frágil; mostrar quantos meses entraram na média.

### #118 (15.5) Faturas e parcelas, com projeção

- **Já coberto.** Faturas fechadas e aberta, detalhe por grupo, projeção pelas parcelas futuras, lista de parcelas em andamento.
- **Sugere acrescentar.**
  - **Card da fatura aberta** no formato do Anotadin: total parcial, "fecha em N dias", "vence dd/mm", número de compras.
  - **Rótulo "3/10"** nas listas de transações do cartão.
  - **Aviso antes do vencimento** ("fatura vence amanhã, R$ X") no `bogle check`.
  - **Comprometido futuro**: soma das parcelas a vencer por mês, e o total, como fração da receita média. É o que o "cabe?" do Anotadin não mostra.
- **Riscos.** A data de fechamento muda (fim de semana, feriado, troca do dia de vencimento). Projetar com o fechamento do último `/bills` e tolerar deslocamento. Parcelas antecipadas ou canceladas somem da Pluggy; a #114 já trata isso no sync.

### #119 (15.6) Orçamento mensal por grupo

- **Já coberto.** Limite fixo por grupo, % usado, saldo, estouro, grupo sem limite fora do orçamento.
- **Sugere acrescentar ou definir.**
  - **Limiar de proximidade em 80%** (o da #119 está "a definir"; o Anotadin usa 80% e 100%).
  - **Teto do mês = soma dos limites** dos grupos com limite, sem um segundo número global para manter.
  - **"Pode gastar no mês" e "por dia"**: (teto menos o gasto nos grupos com limite) dividido pelos dias restantes, contando hoje.
  - Separar os eventos **"o grupo estourou"** e **"o mês estourou"**, e acrescentar **"gasto único grande"** (acima de um valor ou de um percentual do limite do grupo).
  - Texto curto que deixe claro que o limite não é meta.
  - **Recomendação para a questão em aberto da #112** (alerta no `bogle check`): incluir. O Anotadin notifica fatura vencendo, resumo semanal e aproximação do limite. Disparar só na **mudança de faixa** (cruzou 80%, cruzou 100%), uma vez por grupo e por mês.
- **Riscos.**
  - Alerta repetido a cada execução agendada: precisa guardar a última faixa avisada por grupo e mês.
  - Estorno que devolve o grupo para baixo de 100% e depois sobe de novo gera aviso duplicado.
  - Com o atraso de 24 h da Pluggy o aviso chega depois do gasto. O Anotadin avisa no ato porque é manual; o bogle não tem como, e o texto não deve prometer o contrário.

### #120 (15.7) TUI: modos Investimentos e Gastos

- **Já coberto.** Modo Gastos com resumo, orçamento, tendência, faturas, parcelas, transações com correção e regras, gestão de grupos.
- **Sugere.**
  - Ordem da tela inicial do modo Gastos, inspirada na home do Anotadin:
    1. número principal (pode gastar ou sobra) com dias restantes e o selo de ritmo;
    2. o mês em uma conta;
    3. limites com barra;
    4. fixos pendentes;
    5. fatura aberta;
    6. transações recentes.
  - **Seletor de mês** (‹ mês ›) em todas as telas do modo.
  - Transações com busca por descrição, grupo ou conta e filtro gasto/receita.
  - Correção de categoria mostrando o efeito no orçamento.
  - Faixas de estado (tudo certo, no ritmo, atenção, quase lá, estourou) viram cor, sem texto extra.
- **Riscos.** Home carregada demais contraria o "um número, não dez", que combina com as regras de texto do bogle (valor uma vez, sem legendas). Mascote, voz e humor não combinam com "avisos tersos" e ficam de fora.

## 6. Ideias fora do escopo atual

Esforço relativo ao bogle: **P** = pequeno (cabe numa issue curta, sobre dados que a v0.10 já terá); **M** = médio (uma issue cheia, com regra nova ou schema); **G** = grande (épico ou várias issues).

| # | Ideia | Justificativa | Esforço |
|---|---|---|---|
| 1 | **Pode gastar no mês e por dia** (safe-to-spend) | O conceito central do Anotadin e o que atraiu interesse. Sai da #119 mais o atributo `fixo`/`dia_a_dia` da #116. | P |
| 2 | **Ritmo do mês** | Um selo que antecipa o estouro antes dos 80%. Cálculo trivial sobre a #117. | P |
| 3 | **Fixos detectados automaticamente**, com estado pago ou pendente no mês | O Anotadin pede cadastro manual; com a Pluggy dá para detectar (mesmo CNPJ ou descrição, valor parecido, mensal) e marcar como pago quando a transação chega. Dá os "fixos que faltam" da fórmula. | M |
| 4 | **Assinaturas**: lista, total mensal e anual, aumento de preço, assinatura parada | A landing usa "a assinatura que você esqueceu" como gancho. Depende da 3. | M |
| 5 | **Sobra do mês → aporte** | Liga gasto a investimento: a sobra projetada (ou realizada) vira o valor padrão do `bogle suggest`. Mostra no fim do mês "sobrou R$ X, aportado R$ Y". O Anotadin não tem nada do lado do investimento. | M |
| 6 | **Taxa de poupança mensal** e tendência | (receitas − gastos) / receitas, por mês. Um número que resume a saúde do mês e conecta com a carteira. | P |
| 7 | **Reserva de emergência em meses de custo** | Gasto essencial médio (grupos `fixo` mais os essenciais do dia a dia) contra o valor dos ativos marcados como reserva na carteira. "Sua reserva cobre 7,2 meses." | M |
| 8 | **Gasto anual → meta de independência e saque** | O gasto anual real alimenta o alvo de patrimônio (gasto anual / taxa de retirada) e confere o saque anual pós-aposentadoria do glide path com o custo de vida medido. | M |
| 9 | **Fluxo de caixa projetado** até o fim do mês e nos próximos 3 meses | Saldo da conta mais receitas recorrentes menos fixos pendentes, faturas a vencer e parcelas. É a "Sobra contando o que ainda vai entrar e sair", com dados reais. | G |
| 10 | **"Cabe?"** como cálculo determinístico | `bogle spending fits 600 --installments 6`: mostra o efeito no por dia deste mês e o comprometido nos próximos. É o agente do Pro sem IA, e cobre o caso que ele ignora (parcelado). | P |
| 11 | **Resumo semanal e aviso de fatura** no `bogle check` | Segunda-feira: total da semana, maior gasto, grupos acima de 80%. Na véspera do vencimento: valor da fatura. | P |
| 12 | **Custo de oportunidade** de um gasto | Variante do "custo em horas" que usa o que o bogle sabe: R$ X hoje equivalem a R$ Y na aposentadoria, à taxa real esperada da carteira. Pode ser só uma coluna opcional em parcelas e assinaturas. | P |
| 13 | **Lançamento manual** para dinheiro vivo e contas fora do Open Finance | O que não passa pela Pluggy some das análises. O Anotadin mostra que um lançamento curto é viável. | M |
| 14 | **Reembolsos e racha de conta** | Vincular um Pix recebido a um gasto (total ou parte) para que o grupo mostre o custo líquido. | M |

Ordem sugerida: 1, 2, 11 e 6 são baratos e saem quase de graça da v0.10. 3 e 5 dão o maior ganho de produto. 7 e 8 são o diferencial de um app que já tem a carteira.

## 7. Fontes

Acessadas em 27/11/2026, salvo indicação.

- [S1] Landing do Anotadin: https://anotadin.com/
- [S2] Script público da landing (demo do mascote, faixas de preenchimento, questionário da lista de espera): https://anotadin.com/main.js
- [S3] Configuração pública da landing (`appUrl` vazio, "o app ainda não está no ar"; contador da lista a partir de 100): https://anotadin.com/config.js
- [S4] Política de privacidade da lista de espera, atualizada em 17/11/2026: https://anotadin.com/privacidade.html
- [S5] Sitemap: https://anotadin.com/sitemap.xml
- [S6] Wayback Machine, 18/11/2026 (página "Em breve"): https://web.archive.org/web/20261118042404/https://anotadin.com/
- [S7] Post do fundador, 17/11/2026: https://x.com/caiaop_/status/2122493086274109520 (métricas via https://api.fxtwitter.com/caiaop_/status/2122493086274109520)
- [S8] Vídeo 1 do post (42,9 s, gravado em 16/11/2026): telas Início, Transações, Planejar e Novo lançamento do protótipo "Skunk". https://x.com/caiaop_/status/2122493086274109520/video/1
- [S9] Vídeo 2 do post (23,1 s): onboarding, instalação como PWA, cadastro "Só pra convidados". https://x.com/caiaop_/status/2122493086274109520/video/2
- [S10] Prints 3 e 4 do post (catálogo de estados do mascote com gatilhos): https://x.com/caiaop_/status/2122493086274109520/photo/3 e https://x.com/caiaop_/status/2122493086274109520/photo/4
- [S11] Post de 19/11/2026 (anúncio do Anotadin, nova equipe e identidade): https://x.com/caiaop_/status/2123488173250506844
- [S12] Respostas ao post de 17/11, coletadas via https://api.fxtwitter.com/2/conversation/2122493086274109520 (paginado por cursor)
- [S13] Perfil do fundador: https://x.com/caiaop_
- [S14] Instagram: https://www.instagram.com/anotadin.app/
- [S15] Busca na App Store, Brasil (0 resultados): https://itunes.apple.com/search?term=anotadin&country=br&entity=software
- [S16] Busca no Google Play (nenhum app com esse nome): https://play.google.com/store/search?q=anotadin&c=apps&hl=pt_BR
- [S17] Busca no Reclame Aqui (403 para acesso automatizado; buscas na web sem resultado): https://www.reclameaqui.com.br/busca/?q=anotadin
- [S18] Protótipo removido (404 `DEPLOYMENT_NOT_FOUND`): https://skunk-public.vercel.app/
- [S19] Homônimo sem relação (app de notas): https://github.com/mauricioaugustobr/anotadin
- [S20] Contexto interno: issues #112 (com os comentários de discovery, arquitetura e escopo) e #114 a #120 do bangoim/bogle

Respostas citadas (links sem o nome dos autores, que são pessoas físicas):

- [R1] https://x.com/i/status/2122572083620397250
- [R2] https://x.com/i/status/2122703327527874756
- [R3] https://x.com/i/status/2122698342794932692
- [R4] https://x.com/i/status/2122725899132952625
- [R5] https://x.com/i/status/2122708998248161525
- [R6] https://x.com/i/status/2122687910499549432
- [R7] https://x.com/i/status/2122690572356059543
- [R8] https://x.com/i/status/2122792076085719537
- [R9] https://x.com/i/status/2122702006427029759
- [R10] https://x.com/i/status/2122731347873853535
- [R11] https://x.com/i/status/2122878016300949891 e https://x.com/i/status/2123033820299710589
- [R12] https://x.com/i/status/2123587409791406382
- [R13] https://x.com/i/status/2122737513131647401
- [R14] https://x.com/i/status/2123493325789385000
- [R15] https://x.com/i/status/2122698723218108835
- [R16] https://x.com/i/status/2122690526742765901
- [R17] https://x.com/i/status/2122689160112918790

## Apêndice: método e o que ficou sem verificar

- **Método.**
  - Landing, script, configuração e privacidade baixados com `curl` e lidos na íntegra.
  - Frames dos dois vídeos extraídos com `ffmpeg` (0,5 a 2 quadros por segundo) e lidos visualmente.
  - Prints conferidos com OCR (`tesseract`).
  - Respostas paginadas pela API pública do FxTwitter e classificadas por palavras-chave, com revisão manual das que não caíram em nenhum tema.
  - Nenhuma conta foi criada e nenhum dado pessoal foi enviado.
- **Sem verificar.**
  - Áudio dos vídeos (não transcrito).
  - Tamanho da lista de espera: o contador da landing depende de uma chamada ao backend que não foi feita.
  - As 30 citações do post e as cerca de 136 respostas que a API não devolveu.
  - Outros posts do fundador sobre o produto: a linha do tempo pública do X recusou acesso automatizado (HTTP 429).
  - Posts do Instagram (nenhum visível).
  - Tudo o que mudou entre o protótipo "Skunk" e o app que vai ao ar.
