# Análise de conexão e recuperação — 1.5.0

## Diagnóstico ao vivo posterior — 1.5.0

A 1.4.0 não resolveu a espera observada nesta máquina. Com diagnóstico local
restrito ao loopback, o auxiliar já estava aquecido em cerca de 141 ms, mas o
Discord permanecia em `AWAITING_ENDPOINT`. O evento `STREAM_CREATE` chegava;
`STREAM_SERVER_UPDATE`, que entrega o endereço de mídia, demorou cerca de
149 segundos em uma tentativa. Depois de recebê-lo, a abertura do controle
levou aproximadamente 954 ms e a descoberta UDP, 250 ms. A fila JavaScript
dos eventos levou milissegundos. Isso localiza a espera anterior à mídia;
reduzir apenas o aquecimento do auxiliar não basta para essa situação.

A comparação usa sessões novas, a mesma fonte de captura e a região Brazil,
com o mesmo processo Discord já aberto:

| Rota | Endereço de mídia | Conexão de mídia |
| --- | --- | --- |
| Direta, primeira | Ausente após 20,35 s | Ausente após 20,35 s |
| Proton, primeira | 1,263 s | 2,897 s |
| Proton, segunda | 1,204 s | 2,815 s |
| Direta, segunda | 0,716 s | 2,323 s |

A rota direta também conectou rapidamente. A comparação é pequena e sequencial;
não elimina aquecimento, cache ou variações do servidor. Os dados não demonstram
que a rota direta sempre falha, nem que o túnel seja sempre mais rápido.

Uma abertura nova da 1.4.0, reiniciando também o auxiliar, recebeu o endereço
em 1,045 s e conectou em 3,431 s. Portanto, a hipótese de aquecimento não foi
descartada e os resultados rápidos não são exclusivos da rota pelo Proton.

Após corrigir a espera pelo socket inicial e reiniciar novamente o Discord,
o Gateway migrou uma vez e permaneceu conectado pelo auxiliar. Nas aberturas
manuais seguintes, foram observados eventos de endpoint em cerca de 525 ms
para recepção e 534 ms para envio. Envio e recepções chegaram a `RTC_CONNECTED`;
duas recepções sinalizaram entrega do primeiro quadro e os contadores dos vídeos
avançaram. Não foi obtido um tempo preciso de primeiro quadro remoto enviado.
Essas observações validam funcionamento nesta chamada, sem prometer a mesma
latência em outras redes ou uma vantagem exclusiva sobre a rota direta.

`RTC_CONNECTED` não comprova primeiro quadro remoto. A tentativa de medir o
primeiro quadro da prévia por callback não retornou um tempo válido, portanto
não foi substituída pelo tempo de conexão nos resultados. Contadores da prévia
avançaram 36 quadros em cerca de 1,2 s numa consulta posterior; isso tampouco
comprova que outro participante recebeu o vídeo.

Alternar o Gateway de volta à rota direta invalidou a sessão em uma tentativa
e encerrou o compartilhamento. A implementação mantém uma rota durante a
execução do Discord, aguarda o auxiliar e migra apenas a conexão antecipada
que o cliente já tenha aberto. Reconexão, autenticação, compressão, ETF e
retomada continuam sob responsabilidade do Discord; o auxiliar apenas
encaminha os frames uma vez, com fila limitada e contrapressão.
Os códigos de fechamento são encaminhados nos dois sentidos antes de liberar
a conexão: substituir um pedido de retomada 4000 por fechamento normal 1000
pode invalidar a sessão no servidor. Um teste verifica esse caminho real.

O escopo aumentou explicitamente: **o Gateway principal também usa o Proton**,
incluindo seus eventos de mensagens, presença e chamadas. A voz principal,
HTTP/CDN e os demais aplicativos continuam diretos. Rotas e DNS do sistema
não são modificados. Hosts, portas e parâmetros do Gateway são restritos,
a origem precisa ser a do Discord e cada conexão exige autorização local.
Conteúdo, tokens e identificadores não entram no diagnóstico.

Os testes novos verificam mensagens textuais e binárias acima de 1 MiB sem
alteração ou repetição, fechamento do servidor, rejeição de destinos e origens,
limite de duas conexões Gateway, isolamento dos contadores de mídia e espera
pelo auxiliar. O controle de mídia mantém limite de 1 MiB. Contadores Gateway
não comprovam tráfego UDP saudável e não impedem a recuperação da mídia.

## Revisão de 05/11/2026 — inicialização e perda silenciosa de UDP

Foram relatados erros de tela preta na inicialização e após 30–60 minutos,
aliviados temporariamente por reparo/reinício, e congelamentos que exigiam
reabrir a transmissão. Não foi coletado diagnóstico do PC afetado. O intervalo
relatado, sozinho, não identifica a causa nem comprova expiração do WireGuard.

Na instalação local, foi reproduzida uma falha independente de inicialização:
o bootstrap acessava `findStore` antes de os módulos do Discord estarem prontos,
esgotando a busca lazy de Flux e quebrando componentes do Vencord até recarregar.
A versão 1.5.1 usa `Webpack.waitFor` para aguardar `connectStores`, sem consumir
essa busca antecipadamente. Não aguarda `CONNECTION_OPEN`, pois exigir conexão
direta antes de instalar a rota do Gateway bloquearia o uso do próprio túnel.

O relay também tinha uma lacuna verificável: após a descoberta inicial, a tarefa
de descoberta ficava suspensa para sempre. Uma associação UDP que deixasse de
entregar pacotes, mantendo seu controle TCP aberto, não encerrava nenhuma tarefa.
O primeiro pacote recebido continuava protegendo essa ponte como saudável,
mesmo após perda definitiva do caminho. Os testes novos reproduzem esse estado.

Depois de uma descoberta bem-sucedida, a ponte verifica períodos sem UDP recebido
em intervalos de 30 segundos. Somente quando falta tráfego recente, envia a mesma
consulta de descoberta, até três vezes em oito segundos. Tráfego que volta a
chegar também confirma o caminho. Respostas da verificação são consumidas pelo
relay; a resposta solicitada pelo próprio Discord continua sendo encaminhada.
Pacotes de mídia, autenticação e DAVE não são repetidos ou decodificados.

Se nenhuma resposta chegar, a manutenção descarta apenas a ponte afetada e fecha
seu controle com `4006`, solicitando nova negociação nativa. Mudança do mapeamento
externo de IP/porta faz o mesmo: a negociação anterior não pode continuar usando
o endereço antigo. Endereços ficam somente em memória e não são diagnosticados.
Falhas de transporte também agendam a verificação do túnel em segundo plano.
As proteções de outras transmissões, duas verificações malsucedidas e intervalo
entre reinícios continuam aplicadas. Tarefas encerradas não protegem uma ponte
com base em tráfego histórico. Vídeo pausado que responde à consulta não é
reconectado apenas por estar sem quadros.

Os contadores `udpPathChecks`, `udpPathProbes`, `mediaPathTimeouts` e
`mediaPathChanges` e os eventos `media-path-timeout`/`media-path-changed` permitem
distinguir essa recuperação no relatório filtrado. Nenhum payload é registrado.

A validação inclui perda silenciosa após 120 ciclos saudáveis acelerados
(equivalentes a uma hora da cadência nominal, sem esperar uma hora real), perda
temporária de respostas, retomada de mídia durante a consulta, mudança de porta,
cancelamento, fontes/SSRC incorretos e migração pelo instalador sem alterar a
configuração privada. A espera de inicialização também é testada com o Gateway
direto incapaz de completar `CONNECTION_OPEN`.

São testes determinísticos do transporte, não uma reprodução de uma chamada de
uma hora no computador relatado. Receber UDP não comprova imagem decodificada;
congelamentos de codec, GPU, DAVE ou do transmissor podem exigir investigação
separada. Não foi incluída reconexão periódica de transmissões saudáveis.

Referências de protocolo: [descoberta UDP e negociação do Discord](https://docs.discord.com/developers/topics/voice-connections#ip-discovery)
e [códigos de fechamento](https://docs.discord.com/developers/topics/opcodes-and-status-codes#voice-voice-close-event-codes).

## Revisão de 31/10/2026

O usuário esclareceu que o vídeo aparece após uma espera, tanto no envio
quanto na recepção. A instalação local usava 1.2.4 e Vencord 8d64c8b; a 1.3.0
continuava como rascunho. O preload ainda continha o carregador. O auxiliar
registrava tráfego UDP nos dois sentidos, sem perda nas filas no instante
consultado. Tráfego UDP não comprova a entrega de quadros decodificados.

Logs locais mostraram uma abertura do controle que fechou cerca de 10,8 s
depois e voltou a negociar; uma tentativa posterior chegou ao estado conectado
em cerca de 1,6 s. Os logs também registraram falhas na busca `connectStores`
do Vencord. Esses dados descrevem a instalação anterior, sem medição comparável
de primeiro quadro antes/depois. Nenhum token, endereço, chave ou identificador
da chamada foi copiado para este documento.

Foram corrigidos três bloqueios verificáveis: ApexExperimentStore usava a
busca quebrada mesmo quando StreamRTCConnectionStore já tinha fallback;
o bootstrap abandonava módulos demorados após 15 s; e a recuperação mantinha
o bloqueio de abertura durante uma consulta de saúde de até oito segundos.
Uma falha transitória agora fecha o controle afetado com 4006 e agenda a
recuperação em segundo plano, permitindo uma negociação nova sem aguardar
essa consulta. O reinício efetivo do núcleo continua exclusivo e mantém
as proteções de sessões saudáveis, duas falhas e intervalo mínimo.

Testes com eventos bloqueiam deliberadamente a consulta de recuperação:
outra tela recebe sua resposta em menos de 0,5 s no teste local, enquanto
a consulta continua pendente. Isso verifica ausência do bloqueio, e não
uma promessa de latência em rede. Outros testes cobrem fechamento imediato,
carregamento tardio de webpack/Apex, getters inválidos, preservação de voz,
migração sem `.conf` e recebimento real de WM_DROPFILES pela janela Tk.

A chave antiga não foi encontrada nos locais de publicação nem na lixeira.
A nova chave foi protegida com DPAPI e enviada para um secret do GitHub;
o manifesto novo é rejeitado corretamente pela chave antiga nos testes.
Por isso a migração para 1.4.0 exige uma execução do pacote novo. Publicar uma
release não permite modificar o código de avisos já instalado em outros PCs.

O auxiliar local já era filho do renderer do Discord e o núcleo era filho do
auxiliar. A criação Windows agora usa `detached: false`. Essa relação não
elimina os processos nem garante o agrupamento visual do Gerenciador de Tarefas.

## Revisão anterior, incorporada nesta release

Revisão de 28/10/2026. Escopo: bootstrap Electron/Vencord, interceptação das
sessões de compartilhamento, auxiliar Python, SOCKS TCP/UDP, núcleo WireGuard,
recuperação, diagnóstico e distribuição. Os caminhos de instalação, seleção de
versão e atualização assinada também foram inspecionados para entender seu
efeito sobre a inicialização. Não houve teste em chamada real nem reprodução
do problema específico relatado pelo usuário que não conseguia assistir.

## Como a conexão funciona

1. O preload inicia `native.cjs`, prepara o auxiliar e injeta o runtime no Discord.
2. O runtime intercepta o Gateway principal e sockets com contexto `stream`.
   A voz principal continua direta. Cada socket de mídia recebe uma identidade
   local para retomadas; o Gateway é encaminhado sem interpretar seu conteúdo.
3. O auxiliar abre o controle WSS do servidor de mídia através do SOCKS local,
   com saída pelo WireGuard em userspace.
4. Ao receber `READY` ou `SFU_UPDATE`, cria uma associação SOCKS UDP e substitui
   o destino fornecido ao Discord por uma porta de loopback.
5. O Discord descobre o IP/porta externos e negocia o protocolo. Datagramas de
   mídia seguem pela ponte. A criptografia e a decodificação continuam no Discord.

Portanto, WebSocket aberto, UDP funcionando e vídeo decodificado são etapas
diferentes. Uma tela preta não identifica sozinha qual delas falhou.

## Problemas encontrados e alterações

| Achado no código anterior | Efeito possível | Alteração |
| --- | --- | --- |
| Aquecimento único na abertura; núcleo e worker encerrados depois de aproximadamente 120 segundos sem uso | Primeira visualização após um intervalo volta a iniciar o túnel do zero | Worker vinculado ao processo proprietário permanece aberto; aquecimento periódico com espera progressiva em falhas |
| Consulta DNS única para aquecer/verificar | Uma perda de datagrama pode marcar a rota como indisponível | Até duas consultas, dentro do limite global da verificação; respostas inesperadas são ignoradas dentro do prazo |
| Uma única tentativa de abertura do controle, com timeout de 15 segundos | Falha transitória é repassada ao cliente, dependendo da reconexão nativa ou de nova tentativa manual | Até duas tentativas de handshake dentro do orçamento original de 15 segundos, separadas por 250 ms, sem repetir mensagens da aplicação |
| Abertura do controle continuava aguardando upstream mesmo depois de sair da transmissão | Trabalho de conexão desnecessário e possibilidade de sessão pendente | Encerramento do socket local cancela a abertura upstream |
| Primeira consulta UDP apenas encaminhada; nenhum acompanhamento de sua resposta | Perda na descoberta inicial pode deixar o controle conectado e a tela carregando | Identificação estrita da consulta de 74 bytes; até três repetições em 250 ms, 500 ms e 1 s; timeout total de oito segundos |
| Controle conectado era suficiente para impedir qualquer recuperação | Negociação presa sem tráfego podia preservar um núcleo sem conectividade | Tolerância de 15 segundos para negociação; depois, proteção baseada em UDP já recebido pela sessão |
| `Core.ensure()` parava e substituía um processo ainda vivo após uma única falha de acesso ao SOCKS | Associações UDP em uso podiam ser invalidadas fora do mecanismo de geração/recuperação | Falha temporária gera erro; substituição de processo vivo fica no caminho de recuperação protegido |
| Até oito sessões retidas por 40 segundos; novas sessões eram recusadas quando o limite estava cheio | Trocas rápidas de tela podiam gerar falhas temporárias | Remoção da sessão inativa mais antiga quando for necessário abrir espaço |
| Exceção síncrona do bridge nativo deixava `record.starting` ativo | Aquele socket podia parar de tentar iniciar o auxiliar | Exceções síncronas passam pela mesma espera progressiva das rejeições assíncronas |
| Exceção da fábrica do socket era tratada como falha do auxiliar | Tentativas desnecessárias de reiniciar a abertura | Erros da fábrica ficam distintos do estado de disponibilidade do auxiliar |
| Diagnóstico agregado não indicava as etapas da mídia e não havia exportação filtrada na interface | Difícil investigar relatos que o desenvolvedor não reproduz | Tempos de controle e descoberta, indicadores por ponte, contadores de repetição/timeout, eventos limitados e relatório para suporte |

Os achados acima são verificáveis no código e nos testes. Sua contribuição
exata para cada relato de tela preta continua sendo uma hipótese até medir
o caso real. A alteração do aquecimento visa reduzir o custo de inicialização,
mas não fornece uma medição de latência em redes reais.

## Proteções de recuperação

- A recuperação mantém duas verificações malsucedidas, intervalo mínimo de
  cinco segundos entre verificações de recuperação e 30 segundos entre
  reinícios. Uma falha isolada não reinicia repetidamente o núcleo.
- Uma transmissão que já recebeu UDP impede que a falha de outra provoque
  reinício global. Essa proteção é conservadora e também cobre vídeo pausado.
  Ter recebido UDP não comprova que houve imagem decodificada.
- Uma ponte com timeout na descoberta é descartada antes de solicitar sessão
  nova, com fechamento `4006`. O Discord continua responsável por refazer
  Identify/Resume, tokens, heartbeats e negociação de mídia.
- Esse timeout também agenda uma verificação de recuperação em segundo plano.
  Abrir somente o controle TCP não apaga falhas anteriores da verificação UDP.
  A verificação continua sujeita às proteções e intervalos mínimos existentes.
- A verificação periódica não mantém o bloqueio de recuperação durante a
  espera DNS. Uma transmissão pode abrir seu controle enquanto esse teste espera.
- Resultados tardios de uma verificação de geração anterior não sobrescrevem
  a saúde do núcleo recuperado.
- Sessões com `READY` novo continuam recriando a ponte; retomadas válidas
  preservam sua identidade. Protocolos v8/v9/v10 e frames binários permanecem
  cobertos pelos testes existentes.
- Destinos, portas, origem Discord e autorização local continuam restritos.
  Não foi introduzida saída direta de mídia fora do túnel em caso de falha.

## Desempenho e custo

O caminho de mídia continua sendo encaminhamento de bytes, sem decodificação,
cópia para disco ou inspeção do vídeo. A tarefa de descoberta fica suspensa
após concluir; as repetições só ocorrem antes da resposta inicial. Não há timer
por pacote nem reconexão causada simplesmente por vídeo silencioso/pausado.

O aquecimento usa uma verificação a cada 30 segundos após sucesso. Em falhas,
a espera sobe de 5 para 10, 20, 40 e 60 segundos. Cada verificação tem timeout;
consultas concorrentes de aquecimento compartilham o mesmo bloqueio. O
keepalive WireGuard existente permanece configurado pelo `.conf`, com padrão
de 25 segundos quando não há valor explícito. Não foi alterado o valor de
configurações já instaladas.

Permanecem os limites existentes de dois workers, `GOMAXPROCS=2`, limite suave
de memória Go de 128 MiB, filas limitadas e até quatro controles ativos. Esses
valores não são garantia de teto rígido de CPU/RAM. Manter o túnel pronto
exige memória e pequenos pacotes mesmo quando ninguém assiste a uma tela;
há uma troca explícita entre esse custo e a latência de uma conexão fria.
Não foi medido impacto em jogos ou computadores de baixo desempenho.

## Diagnóstico do usuário que não consegue assistir

Com o Discord aberto, reproduzir a falha e usar **Verificar** no instalador.
O arquivo `data/diagnostico.json` contém uma lista permitida de campos. Não
copia o `.conf`, configuração do núcleo, URLs, endereços, identidades de
sessão, caminhos do usuário, payloads, tokens ou chaves. O histórico tem até
64 eventos e até oito sessões; versões antigas do status são toleradas.

No Linux, `python3 ~/.local/share/discord-stream-tunnel/launcher.py diagnose`
gera o mesmo relatório. No Windows, o executável aceita `diagnose`. Ajustar o
caminho Linux quando `XDG_DATA_HOME` foi personalizado.

| Evidência | Interpretação e próximo passo |
| --- | --- |
| `available=false` ou `stale=true` | Não há amostra recente. Confirmar que a integração carregou e coletar durante a falha |
| `coreRunning=false`, eventos de erro de inicialização | Verificar a instalação, permissões e interferência no executável; reaplicar pelo instalador se necessário |
| `tunnelReachable=false` | Falha de conectividade pelo túnel; comparar outra rede e um `.conf` novo de outro servidor Proton |
| Muitas ocorrências `control-retry`/`control-error` | Falha no controle via SOCKS, DNS, TLS ou endpoint. Correlacionar classe do erro e horário |
| `mediaAllocated=true`, `localPeerSeen=false` | O Discord ainda não enviou UDP para a ponte local; investigar integração e permissões locais |
| `udpSent=true`, `udpReceived=false`, `media-startup-timeout` | Consulta inicial enviada, sem resposta válida a tempo. Investigar rota UDP/Proton/rede |
| `udpReceived=true` e imagem preta | Transporte recebeu bytes, mas isso não prova decodificação. Investigar Discord/Vencord, DAVE, codec e remetente |
| `udpQueueDrops` ou `udpReceiveQueueDrops` aumentando | Filas Python saturadas; comparar carga do computador e bitrate. Não mede perdas anteriores ao socket/na operadora |

Pedir junto do relatório: horário aproximado, sistema, versões do
Discord/Vencord, código do erro, se afeta todas as transmissões ou somente
uma e resultado em outra rede/servidor Proton. Não solicitar o `.conf`.

## Validação

Resultados locais em Windows: **50 testes Python aprovados em Python 3.10.6
e 3.14.8**, além dos **sete scripts de teste Node**. Build PyInstaller 6.22.3
concluído. O executável empacotado executou `status` e `diagnose` em um perfil
isolado. Pacotes Windows/Linux e os hashes dos 165 arquivos do bundle de
runtime foram conferidos; o guia HTML acompanha ambos e é gerado separadamente.
Os pacotes locais não representam uma publicação nem um manifesto novo assinado.

Testes usam servidores locais e falhas sintéticas, sem credenciais de conta.
Cobrem handshake transitório, envio único de Identify, falha terminal,
cancelamento, aquecimento lento concorrente, espera progressiva, ciclo de
vida com proprietário, geração antiga, proteção de transmissão estabelecida,
congestionamento temporário do SOCKS, limite de sessões, perda da descoberta,
resposta com SSRC incorreto, timeout e descarte da ponte, filtragem do relatório
e inclusão do guia nos dois pacotes e como arquivo separado.

A suíte existente continua cobrindo integridade binária/DAVE, associação
SOCKS autenticada, endpoints restritos, retomada, mudanças de SFU, atualização
assinada, reparo de preload, interface e preservação de atalhos. Testes novos
do runtime também cobrem exceção síncrona do helper, erro da fábrica e remoção
durante inicialização.

Para validar a melhora percebida, ainda é necessário comparar em chamadas
reais: primeira visualização após abrir o Discord, após mais de dois minutos
sem transmissão, envio e recepção com ambos usando a integração, trocas de
tela, vídeo pausado, troca de Wi-Fi, suspensão/retomada, servidor Proton fora
do ar e computador sob carga. Medir tempo até primeiro quadro e continuidade
do vídeo. Não usar apenas `warmupMs` ou `discoveryMs` como substitutos dessa medida.

## Limites restantes

Não existe garantia de conexão sem quedas: disponibilidade do servidor,
bloqueio de UDP pela rede, perda de pacotes, Wi-Fi, operadora, credenciais
revogadas e mudanças nas APIs internas do Discord continuam externos ao
controle do projeto. Não foi adicionado reinício por simples inatividade de
vídeo, pois uma transmissão pausada pode ficar legitimamente silenciosa.

A proteção conservadora de sessões que já receberam UDP pode adiar um
reinício global se todas ficarem silenciosamente presas depois disso. Sem
telemetria confiável do cliente, forçar esse reinício poderia derrubar uma
transmissão saudável. O diagnóstico permite distinguir esse cenário para
uma investigação posterior, sem alegar que está resolvido.

O helper continua com a busca de módulos e a interceptação em APIs internas
do Vencord/Discord. Sua compatibilidade precisa de validação a cada mudança
relevante desses clientes. O diagnóstico do transporte não confirma sozinho
que o runtime carregou ou que o decodificador apresentou quadros.

## Fontes de protocolo consultadas

- [Discord: voz, descoberta UDP, retomada e frames binários/DAVE](https://docs.discord.com/developers/topics/voice-connections).
- [Discord: códigos de fechamento, incluindo sessão inválida 4006](https://github.com/discord/discord-api-docs/blob/main/developers/topics/opcodes-and-status-codes.mdx).
- [WireGuard: persistent keepalive e intervalo de 25 segundos](https://www.wireguard.com/quickstart/).
- [sing-box: endpoint WireGuard em userspace, workers e keepalive](https://sing-box.sagernet.org/configuration/endpoint/wireguard/).

O timeout local e a política de repetição são decisões deste projeto; não
são garantias de latência documentadas por esses fornecedores.
