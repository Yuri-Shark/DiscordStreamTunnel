# Otimização de várias telas

Otimizações incluídas na versão 1.5.9 para a edição com Vencord. A validação
ao vivo ainda encontrou travamentos. Os ajustes não garantem eliminar perdas
no caminho externo.

Quando há pelo menos duas recepções visíveis pelo túnel, a integração passa a
coordenar os pedidos de qualidade que o Discord envia aos transmissores:

- As telas dividem um orçamento de pixels equivalente a, no máximo, 720p.
  A parcela acompanha a área exibida e o zoom; uma tela maior recebe mais.
  O pedido também diminui para grades pequenas, mesmo abaixo desse teto.
- Quando o transmissor anuncia uma camada de qualidade menor, ela é escolhida
  para as telas menores. Uma tela em destaque pode preservar a camada original
  até haver perda persistente mais forte. Os limiares de área de 55%/60%
  evitam alternar de camada quando o tamanho das telas varia por poucos pixels.
- Duas amostras consecutivas com pelo menos 8% de perda recente reduzem o
  orçamento. Há três níveis de redução: 65%, 40% e 25% do orçamento inicial.
  A consulta já existente ocorre a cada cinco segundos. A recuperação de um
  nível exige seis amostras com até 2% de perda e avanço de quadros em todas
  as telas; estatísticas ausentes ou travadas não comprovam recuperação.
- Com apenas uma recepção solicitada, o pedido original do Discord volta.
  Telas ocultas mantêm zero pixels; telas reconectando preservam sua parcela
  enquanto continuam solicitadas, evitando picos de demanda nas vizinhas.

O ajuste acontece por pedidos nativos de mídia. Não requer sair e entrar nas
telas. Voz e envio próprio não recebem esses ajustes. Resolução/zoom naturais
da interface não são sobrescritos; a alteração limita o pedido emitido.

O orçamento é uma **solicitação de qualidade**, não um limite garantido de
Mbps ou de resolução decodificada. O Discord instalado ajusta bitrate por
pixel solicitado e aplica seus próprios mínimos; um transmissor com uma única
camada, ou com outros espectadores pedindo mais qualidade, pode continuar
enviando vídeo acima do pedido. Não se cria uma camada que o transmissor não
oferece. Se a API necessária não existir, a integração preserva o comportamento
nativo e registra a quantidade de recepções sem suporte.

## Aplicação e diagnóstico

Os arquivos do preload são carregados ao abrir completamente o Discord.
Modificar os arquivos em disco não atualiza o JavaScript já carregado. Não é
necessário encerrar o jogo, mas uma reabertura do Discord interrompe a chamada;
por isso a atualização é carregada na próxima abertura voluntária.

`data/playback-status.json` e o relatório de **Verificar** incluem `multiScreen`:
quantidade de telas, pedidos limitados, camadas menores selecionadas, nível de
redução e orçamento solicitado. O relatório não contém identificadores de
participantes, SSRCs, conteúdo de mídia ou credenciais.

Para desativar o ajuste de forma persistente, acrescente
`"multiStreamOptimization": false` ao objeto existente em
`data/local-settings.json`, preservando os outros campos, e reabra o Discord.
Não substitua o arquivo inteiro: ele também contém a autorização do auxiliar.
Na sessão, o console permite alternar
`window.__discordProtonStreams.otimizarTelas(false)` ou `true`.

Os testes automatizados cobrem o orçamento agregado, telas em destaque,
camadas anunciadas/inativas, redução e recuperação gradual, contadores que
reiniciam, estatísticas ausentes, telas ocultas, reconexões, envio/voz,
desativação e restauração de métodos alterados por outros plugins.

## Segunda revisão

`multiScreen.revision=2` identifica o código novo carregado na sessão.

- As novas conexões de recepção abrem com espaçamento de 750 ms para reduzir
  a concentração das negociações iniciais. Voz e envio próprio não aguardam
  essa fila. Uma tela fechada ou substituída antes de sua vez não é aberta.
  Esse espaçamento não garante separar todos os keyframes: o tempo de cada
  negociação também depende da rede e do Discord.
- Alterações inferiores a 10% no pedido, sem mudança de camada, não provocam
  reenvios adicionais pelo controlador. Quando há uma alteração relevante,
  todas as parcelas são recalculadas juntas. Isso evita aplicar aumentos
  isolados e ultrapassar o orçamento por ignorar pequenas reduções nas outras.
- O observador se conecta ao gerenciador de qualidade já na atribuição do
  socket. Isso cobre metadados de camadas que chegam antes do próximo evento
  do store. A substituição do gerenciador preserva o limite que já estava ativo.
- Vídeo parado com pelo menos 8% de perda recente recebe até 30 segundos
  adicionais de estabilização antes da reconexão por decodificação parada.
  O controle de qualidade continua atuando nesse prazo. Timeouts nativos do
  primeiro quadro conservam a recuperação existente; o erro não é ocultado.
- O diagnóstico inclui bitrate, FPS decodificado e perda recente por tela;
  taxas com mais de 15 segundos não são apresentadas como atuais. O total
  medido fica em `multiScreen.measuredBitrateBps`, junto da quantidade de
  recepções medidas. Esses valores são estimativas pelos contadores nativos,
  não incluem toda a sobrecarga do túnel e não impõem um teto de banda.
- `lowerLayerAvailableReceivers` conta os gerenciadores com uma camada menor
  anunciada; `layerInfoPendingReceivers` distingue informação ainda não
  observada de ausência confirmada de camada menor. `networkDeferrals` conta
  os períodos de estabilização antes da recuperação por decodificação parada.

Na sessão real com a segunda revisão, as três recepções alternaram reprodução
normal com períodos de FPS decodificado zero e perda recente elevada. Nenhum
dos três transmissores anunciou uma camada menor. A revisão 2 não eliminou os
travamentos; a ausência de erros no relay não localiza toda a perda externa.

## Terceira revisão

`multiScreen.revision=3` identifica a correção da reserva durante reconexão.
A revisão anterior verificava a URL do socket para contar uma tela: enquanto
o Discord removia o socket antigo e criava o novo, a tela saía do orçamento.
Se isso ocorresse em várias telas, o nível de redução voltava a zero e as
vizinhas recebiam pedidos maiores durante o congestionamento.

Agora a última demanda de uma recepção conectada fica reservada por até
30 segundos durante essa troca, inclusive quando o novo gerenciador ainda
tem dimensões zeradas. A reserva não renova seu próprio prazo. Fechar a tela,
pausar a recepção ou ocultar uma tela no mesmo gerenciador libera sua parcela;
desativar a otimização também remove as reservas. A reserva expirada não
mantém uma tela desconectada indefinidamente.

Os testes reproduzem o intervalo sem socket, a troca do gerenciador, a
preservação do nível de redução, a pausa e o vencimento da reserva. Essa
correção evita um aumento indevido da demanda; não comprova resolver a perda
de pacotes que já foi observada antes das reconexões. O código é carregado
na próxima abertura completa do Discord, sem reinício automático da chamada.

O reteste de 19/11 confirmou a revisão 3 carregada, mas ainda encontrou
travamentos em duas recepções durante o jogo.
O controle não deve ser apresentado como correção definitiva dessas perdas.
