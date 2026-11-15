# Travamentos durante jogos — observação de 14/11/2026

Estado: duas falhas locais corrigidas; travamento original ainda não resolvido.
Esta investigação não é uma garantia de estabilidade nem uma nova release.

## Ambiente e reprodução

- Discord desktop com Vencord e DiscordStreamTunnel 1.5.8, worker `selector`.
- Duas recepções H264 e um envio próprios, com jogo aberto em outro monitor.
- Recepção por software (`ffmpeg`); aceleração de hardware do Discord desativada.
- Intel i5-12400F e NVIDIA RTX 3070 Ti.

Uma amostra de aproximadamente 15 segundos registrou somente dez quadros novos
em cada recepção, enquanto os contadores de perda aumentaram em 1.467 e 1.147
pacotes. As transmissões também apresentaram intervalos com quadros avançando
normalmente. Portanto, uma leitura isolada de 30/60 FPS não confirma a solução.

## Testes e evidências

| Teste | Resultado | Limite da conclusão |
| --- | --- | --- |
| Filas do relay e erros UDP do Windows | Sem descartes/erros contabilizados durante a amostra | Não mede os descartes dentro do núcleo ou fora do computador |
| Prioridade temporária `AboveNormal` no auxiliar e núcleo | Não eliminou o problema; valores restaurados para `Normal` | Não justifica mudar permanentemente a prioridade |
| Estatísticas nativas a cada dois segundos | Consultas responderam em aproximadamente 1–2 ms durante congelamentos | Não é uma medição de todos os threads do processo |
| Limitação de segundo plano desativada temporariamente | Travamentos e perdas reapareceram; teste encerrado | Não justifica incorporar essa alteração como correção |
| DNS pelo SOCKS/WireGuard existente | 40/40 respostas pequenas e 40/40 respostas de até 1.187 bytes; mediana aproximada de 137 ms | Não reproduz o caminho SFU–Proton, a taxa ou as rajadas do vídeo |
| Rede local | Gateway respondeu em 1–2 ms; sem descartes/erros registrados na interface Ethernet | Não comprova ausência de perda em todo o caminho |
| Uma recepção com mídia direta e controle pelo túnel | Nenhum quadro utilizável; teste revertido | Não oferece uma alternativa direta validada |

Foi iniciado um relay temporário, autenticado e limitado ao loopback, reutilizando
o SOCKS e o núcleo existentes. Somente uma recepção foi reconectada por ele.
Não foi iniciado outro túnel WireGuard com a mesma chave.

O observador contou sequências RTP **imediatamente após a recepção do SOCKS**,
antes da entrega ao Discord. Na amostra final, o fluxo de vídeo com payload type
105 tinha 11.614 pacotes observados e 4.922 lacunas de sequência. Essa é uma
contagem de lacunas, sem descontar eventual recuperação por RTX; não deve ser
apresentada como taxa final de perda irrecuperável. Não foram exportados SSRCs,
sequências individuais, endereços, identificadores de participantes ou conteúdo.

Nessa mesma amostra, as filas e erros do relay temporário permaneceram zerados.
O maior datagrama observado tinha 1.217 bytes. Isso confirma que a lacuna já era
visível antes do encaminhamento local ao Discord; ainda não distingue núcleo,
servidor Proton, caminho de rede, SFU e transmissor. Não comprova uma falha de GPU,
nem permite atribuir toda a perda ao Proton.

## Reprodução sem perfil Proton

O problema também foi relatado com outros usuários e outros `.conf`.
Foi acrescentado `scripts/benchmark_wireguard.py`: dois processos sing-box
oficiais, chaves de teste geradas na execução, pares em loopback e eco UDP
local. O teste não carrega configurações de conta nem altera rotas do Windows.

Com três fluxos de 500 datagramas/s, rajadas de 128, doze segundos e MTU 1280,
o caminho SOCKS UDP enviou 18.048 datagramas, mas somente 17.883 apareceram na
saída WireGuard do cliente. As filas do relay registraram zero descartes.
Isso reproduz uma perda dentro do caminho local antes da criptografia, sem
servidor Proton, SFU, jogo ou Internet. A perda variou entre execuções; a
quantidade não deve ser tratada como constante. A atualização isolada do
sing-box 1.14.3 para 1.14.4 também não eliminou essa perda no ensaio.

Com UoT v2 no trecho local, a implementação final entregou os 18.048
datagramas à saída WireGuard do cliente. O retorno completo foi de 17.957:
o ensaio ainda perdeu pacotes no lado servidor sintético, antes da entrega
ao relay cliente. Portanto, este resultado não comprova entrega perfeita
de ponta a ponta nem atribui toda a perda real ao transporte corrigido.

O benchmark antigo com SOCKS simulado não atravessava o núcleo real e não
detectava esse problema. Os dois ensaios medem caminhos diferentes.

## Alterações e aplicação local

A mídia agora usa quadros UoT v2, autenticados por SOCKS, em TCP restrito ao
loopback. Há uma conexão por associação, preservação dos datagramas,
limites de escrita, timeout e encerramento em falha. O WireGuard externo
continua transportando UDP. Não há reenvio de mídia pelo aplicativo,
mudança de endpoint ou alternativa direta automática.

A recuperação também foi corrigida: `RESUMED` sem a ponte de mídia pede
renegociação com código 4006. Antes, reiniciar o auxiliar podia manter
o controle conectado sem recriar as portas UDP perdidas. Uma retomada
com ponte válida continua preservando essa ponte.

O código foi aplicado à instalação 1.5.8 com revisão local
`local-media-20261114` e backup externo ao repositório. A chave, o perfil,
as preferências de vídeo e a versão do núcleo instalado foram preservados.
As três pontes foram recriadas pelo UoT após a recuperação. A implementação
compartilhada e o empacotamento da edição independente foram atualizados
localmente; a chamada real com o driver dessa edição ainda não foi validada.
Essa edição experimental já estava no workspace sem rastreamento pelo Git
e fica fora do commit das correções do auxiliar. O teste opcional da integração
é executado quando os módulos dessa edição estão disponíveis.

Validação automatizada: 170 testes Python executados, um ignorado e os
demais aprovados; seis arquivos de testes Node aprovados. Os testes novos
cobrem limites e fragmentação de quadros TCP, IPv4/IPv6, isolamento entre
telas, cancelamento, pressão de escrita, fechamento remoto, autenticação,
integração UDP nativa e recuperação após perda da ponte.

A cópia isolada do conteúdo preparado para commit, sem os módulos experimentais
não rastreados, executou 113 testes Python: 112 aprovados e um teste opcional da
edição independente ignorado. Os seis arquivos de testes Node também passaram
nessa cópia.

Uma segunda variante do ensaio usa o mesmo bind de par único e porta local
efêmera da instalação (`--single-peer`). Com UoT, três fluxos de 500
datagramas/s, rajadas de 128 e sessenta segundos, foram entregues **90.240 de
90.240 datagramas** de ida e volta. O maior atraso foi 31,7 ms; não houve
lacunas nos contadores WireGuard observados nem erros nos núcleos de teste.
No comparativo de doze segundos com SOCKS UDP e esse bind, foram recebidos
18.019 de 18.048 datagramas; 22 já faltavam na saída criptografada do cliente.
Esses resultados comprovam uma melhora local, mas não estabilidade real.

## Limite da validação ao vivo

Antes da aplicação, uma amostra de 45 segundos mostrou somente 132 quadros
adicionais em uma recepção e aumento de 4.206 em `packetsLost`, mesmo sem
descartes no relay. Após reiniciar e recuperar o auxiliar, as duas recepções
ficaram temporariamente sem vídeo solicitado; esse intervalo não constitui
validação de reprodução. É necessário medir ambas solicitando vídeo durante
o jogo e observar novas perdas, quadros e reconexões por um período contínuo.

Na amostra seguinte de 120,7 segundos com UoT, os quadros recebidos avançaram
em 2.570 e 6.010; as perdas nativas aumentaram em 3.470 e 1.627. Os
travamentos, portanto, não foram eliminados pela primeira correção.

Foi acrescentada medição limitada dos cabeçalhos RTP em cada ponte. Ela
mantém uma janela de 2.048 sequências por fluxo, com limites de fluxos e
payload types, e exporta somente contadores agregados. SSRCs, números de
sequência e bytes de mídia não são exportados. RTX é contado separadamente;
as lacunas continuam não sendo uma taxa de perda irrecuperável.

Na comparação sincronizada, as duas recepções indicaram respectivamente
915 e 1.070 pacotes perdidos no Discord, e as mesmas 915 e 1.070 lacunas já
estavam presentes no payload type 105 antes da entrega local. Isso restringe
a perda observada ao caminho anterior ao socket nativo do Discord; não
determina sozinho em qual ponto desse caminho ela ocorreu.

Outros comparativos foram encerrados e revertidos:

- O núcleo oficial 1.13.21, com arquivo compatível e SHA256 verificado, também
  apresentou perdas. O executável oficial 1.14.3 foi restaurado e conferido.
- WireGuard TCP pelo mesmo servidor aceitou tráfego, mas não eliminou as
  perdas e encerrou a sessão durante o ensaio. Não foi incorporado ao produto.
- O comparativo com controle e mídia diretos não produziu vídeo utilizável.
  A configuração original do túnel foi restaurada, com hash idêntico.
- A coleta temporária de erros do núcleo não registrou falhas de envio,
  falta de buffers, timeouts ou erros de leitura. O log temporário foi apagado
  e a configuração de log original restaurada.
- Duas capturas limitadas ao túnel, usando PktMon nas interfaces de rede,
  registraram lacunas em contadores WireGuard. O ETW informou zero eventos e
  buffers perdidos; os quadros observados não estavam fragmentados nem acima
  do MTU Ethernet. A cobertura da captura ainda não foi calibrada contra cada
  datagrama do núcleo, portanto não se atribui toda essa lacuna ao provedor.
  Capturas foram apagadas, a coleta encerrada e o filtro temporário removido.

Uma compilação temporária de diagnóstico do sing-box 1.14.3 foi executada
por aproximadamente 65 segundos, com o mesmo Go 1.26.8 do núcleo oficial.
Ela acrescentou somente contadores internos da pilha UDP; o buffer permaneceu
no padrão efetivo de 212 KiB. Ao final, a pilha tinha recebido 15.226 pacotes
e enviado 13.604, com zero descartes por buffer cheio, erros de checksum,
pacotes malformados ou erros de envio. Na recepção de vídeo observada, o
payload type 105 acumulou 10.515 pacotes e 558 lacunas; RTX, contado
separadamente, acumulou 529 pacotes e 449 lacunas.

Essa medição não sustenta aumentar o buffer UDP como correção. Também não
determina a origem de todas as lacunas: não cobre os descartes anteriores à
pilha UDP nem todos os estágios posteriores. O núcleo oficial 1.14.3 e o módulo
do auxiliar anterior ao ensaio foram restaurados e conferidos por SHA256/bytes.
Nenhuma compilação experimental ou ajuste de buffer ficou aplicado.

As correções locais descritas acima permanecem instaladas, com backup e
manifesto de hashes. O problema original continua sem uma correção completa
validada; os ensaios sintéticos aprovados não substituem essa validação.

Não inserir chaves, configurações privadas ou capturas da tela neste repositório.
