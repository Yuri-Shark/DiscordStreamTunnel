# Análise de conexão e recuperação — 1.3.0

Revisão de 28/10/2026. Escopo: bootstrap Electron/Vencord, interceptação das
sessões de compartilhamento, auxiliar Python, SOCKS TCP/UDP, núcleo WireGuard,
recuperação, diagnóstico e distribuição. Os caminhos de instalação, seleção de
versão e atualização assinada também foram inspecionados para entender seu
efeito sobre a inicialização. Não houve teste em chamada real nem reprodução
do problema específico relatado pelo usuário que não conseguia assistir.

## Como a conexão funciona

1. O preload inicia `native.cjs`, prepara o auxiliar e injeta o runtime no Discord.
2. O runtime intercepta somente sockets com contexto `stream`. A voz principal
   continua direta. Cada socket recebe uma identidade local para retomadas.
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
