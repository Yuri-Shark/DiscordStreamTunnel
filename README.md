# DiscordStreamTunnel

Integração para encaminhar as sessões de compartilhamento de tela do Discord
por um túnel Proton WireGuard em userspace. A voz principal da chamada e os
demais aplicativos continuam pela conexão normal.

O addon deve se limitar ao tunelamento em segundo plano. Os atalhos devem
preservar o ícone original do Discord e qualquer ícone personalizado pelo
usuário. Mudanças de aparência ou de outras funcionalidades do Discord
exigem aviso prévio ao usuário.

## Instalar

Baixe os pacotes em [Releases](https://github.com/Yuri-Shark/DiscordStreamTunnel/releases/latest).
É necessário Discord desktop nativo com Vencord e um `.conf` WireGuard
da **própria conta Proton**. O portal permite gerar configurações no plano gratuito:
[instruções oficiais](https://protonvpn.com/support/wireguard-configurations).

**Windows x64:** extraia o ZIP inteiro, encerre o Discord, execute
`DiscordStreamTunnel.exe`, escolha seu `.conf` e clique em **Instalar / reaplicar**.
O programa inclui Python. Depois abra o Discord pelo atalho normal.

O instalador tem tema escuro, ícone próprio e botões **Instalar / reaplicar**,
**Reparar**, **Verificar** e **Desinstalar**. Reparar restaura o carregador
existente sem pedir novamente o `.conf`.

![Interface do instalador](https://raw.githubusercontent.com/Yuri-Shark/DiscordStreamTunnel/main/assets/installer-preview.png)

**Linux:** use Discord nativo (`pacman`, `.deb`, pacote da distribuição) e Vencord.
É necessário Python 3.10 ou superior; as bibliotecas já estão incluídas.
Flatpak e Snap não são cobertos.

```sh
tar -xzf DiscordStreamTunnel-1.2.3-linux.tar.gz
cd DiscordStreamTunnel-1.2.3-linux
sh instalar.sh
```

No CachyOS/Arch, se faltar Python: `sudo pacman -S --needed python ca-certificates`.
No Ubuntu: `sudo apt install python3 ca-certificates`. Execute a integração
com seu usuário normal. Instale Vencord pelas [instruções oficiais](https://vencord.dev/download/).

No Linux, a interface gráfica é opcional: instale `python3-tk` (Ubuntu) ou
`tk` (CachyOS/Arch) e execute `python3 source/app.py --gui` dentro da pasta
extraída. A instalação por terminal continua funcionando sem Tk.

## O que mudou na 1.2.3

- Reconexões de envio e recepção verificam novamente o auxiliar. Uma abertura
  nova do Discord aguarda o auxiliar da instância anterior encerrar, em vez
  de aceitar um processo que está prestes a sair.
- Falhas de proxy e timeouts passam a verificar a conectividade pelo próprio
  túnel. Duas verificações malsucedidas permitem reiniciar o núcleo que o addon
  iniciou, com intervalo mínimo de 30 segundos e preservando conexões saudáveis.
- Após reiniciar o núcleo, as pontes antigas são descartadas e o Discord recebe
  uma indicação para negociar uma sessão nova. Falhas de conexões anteriores
  não reiniciam o núcleo recém-recuperado.
- O diagnóstico distingue processo aberto de conectividade verificada e inclui
  o processo do Discord responsável, a geração do núcleo e os reinícios.
  Um bloqueio temporário ao gravar o status não encerra o serviço de mídia.
- Testes cobrem concorrência, recuperação após falhas de proxy, proteção de
  transmissões saudáveis e a troca de instância do Discord. Indisponibilidade
  externa do Proton ou da rede ainda pode impedir a conexão; a recuperação
  automática não garante resolver uma queda do servidor ou da operadora.

## O que mudou na 1.2.2

- Reconexões com uma nova sessão de mídia recriam a ponte UDP, mesmo quando
  o servidor continua no mesmo endereço. Isso evita reutilizar a porta antiga
  do Discord e ficar preso no carregamento depois de uma queda. Retomadas
  válidas continuam preservando a ponte existente.
- O encerramento da associação SOCKS pede uma nova sessão, e fechamentos
  normais do servidor são repassados imediatamente ao cliente.
- Datagramas SOCKS inválidos não encerram a recepção. O status registra perdas
  na fila de recepção e um histórico limitado de eventos de conexão, sem
  conteúdo de mídia, tokens ou chaves.
- Testes de regressão cobrem as reconexões v9/v10, a troca da porta UDP local
  e o encerramento do transporte. A correção melhora a recuperação; oscilações
  e perdas na rota de rede ainda podem afetar a transmissão.

## O que mudou na 1.2.1

- A primeira conexão de envio e de recepção aguarda o auxiliar de tunelamento,
  inclusive quando o cliente cria e abre o socket na mesma chamada.
- Compatibilidade com o gateway de mídia v10: `READY` sem endereço inicial,
  atualização de servidor por `SFU_UPDATE` e retirada do endpoint. Os destinos
  continuam restritos aos servidores de mídia e ao endereço local do auxiliar.
- Os atalhos preservam o ícone original ou personalizado. Atalhos de instalações
  antigas sem ícone explícito podem recuperar a referência do backup original.

## O que mudou na 1.2.0

- Interface reorganizada seguindo o estilo do instalador do Vencord, com
  implementação e identidade visual próprias, ícone original e progresso na janela.
- Verificação de novas versões do **instalador Windows** ao abrir, com janela
  **Atualizar e reabrir** / **Agora não**. O pacote é autenticado com a mesma
  chave Ed25519 do projeto antes de baixar, extrair e executar.
- Atualização aceita persistente: se o pacote antigo for aberto outra vez,
  ele revalida o cache e abre a versão nova. As configurações existentes são
  preservadas. Falhas de rede ou assinatura mantêm o instalador atual.
- Gerenciamento dos atalhos pela API nativa do Windows, sem PowerShell,
  preservando os backups de instalações antigas.
- Ícone multirresolução e informações de produto/versão no executável.
  O pacote novo inclui Python 3.14.8. A integração mantém compatibilidade
  com os executáveis instalados anteriormente.
- `SHA256SUMS.txt` acompanha as releases. O executável ainda não tem assinatura
  Authenticode: o aviso do SmartScreen pode continuar. Consulte
  [Segurança e Code signing policy](SECURITY.md).

O atualizador do instalador passa a existir no pacote Windows 1.2.0.
Quem abre um ZIP 1.1.x extraído anteriormente precisa baixar o instalador novo
uma vez para obter esse recurso; a integração dentro do Discord continua
recebendo atualizações automaticamente sem reinstalação.

## Migração do repositório

O repositório e o endereço canônico das atualizações passam a ser
`Yuri-Shark/DiscordStreamTunnel`. Instalações com atualização automática
desde a 1.1.0 continuam recebendo versões novas sem reinstalar ou importar
novamente o `.conf`. A mesma chave pública de assinatura foi preservada.

São publicados dois manifestos assinados para o mesmo pacote:
`update-manifest-v2.json`, consultado diretamente pelas versões novas, e
`update-manifest.json`, compatível com as versões anteriores. Este último
preserva somente o endereço histórico exigido pelos clientes antigos e usa
o redirecionamento do GitHub para o repositório atual. Não crie outro
repositório nos endereços antigos: isso remove os redirecionamentos e
interrompe a atualização de quem ainda não migrou.

Na primeira abertura, a versão antiga baixa e verifica o pacote novo. Na
próxima abertura completa do Discord, ele passa a usar diretamente o novo
endereço. Quem já está na 1.1.2 também pode usar **Reiniciar e aplicar** no aviso.

## Aviso de atualização

Quando uma atualização estiver baixada e verificada, aparece um aviso fixo
no **canto superior direito do Discord**, com **Reiniciar e aplicar** e
**Depois**. O botão verifica novamente se a versão está pronta, preserva o
carregador e usa o reinício nativo do Discord. Reiniciar encerra a chamada e
a transmissão atuais. Não há reinício sem clicar. Se adiar, a versão nova
será usada na próxima abertura completa do aplicativo.

O aviso passa a existir depois que a 1.1.2 estiver carregada. Quem está na
1.1.0 ou 1.1.1 recebe esta versão pelo fluxo anterior e precisa abrir o Discord
novamente uma vez para carregar o recurso; atualizações posteriores mostram o aviso.

## Mudanças anteriores

- O carregador é restaurado depois que o atualizador do Vencord termina de
  escrever os arquivos. A abertura pelo atalho também repara o carregador.
- O helper e a negociação WireGuard são preparados em segundo plano ao abrir
  o Discord. O aquecimento usa uma consulta DNS pelo próprio túnel.
- Conexões concorrentes compartilham a inicialização; falhas do auxiliar têm
  novas tentativas com espera progressiva. A perda do transporte de mídia
  encerra o controle para permitir a reconexão do cliente.
- Código em diretórios de versão separados permite instalar uma atualização
  sem sobrescrever os arquivos de um helper em execução.

## Atualizações automáticas

Na abertura do Discord, a integração consulta a última release pública deste
repositório uma vez por processo do Discord, sem login e sem enviar credenciais.
O manifesto tem assinatura Ed25519. O pacote e cada arquivo também têm SHA256.
Assinaturas inválidas, versões anteriores e caminhos fora da pasta de código
são rejeitados. Falha de rede preserva a versão instalada.

Uma atualização baixada em segundo plano fica pronta para a próxima abertura
do Discord. A partir da 1.1.2, o aviso no canto superior direito permite
**Reiniciar e aplicar** imediatamente ou escolher **Depois**. Não há reinício
forçado. O estado fica em `data/update-status.json`.

Os atalhos compatíveis do Windows são preservados em backup e passam a usar
o iniciador da integração. No Linux, é criado um `discord.desktop` por usuário.
Abrir o executável do Discord diretamente ignora a reparação pelo atalho; a
reparação após atualizações feitas dentro do Vencord continua disponível.
Reinstalações externas do Vencord podem exigir abrir pelo atalho ou reaplicar.

## Funcionamento e remoção

Somente o Discord precisa ficar aberto. O app Proton e o instalador podem
ficar fechados. O auxiliar trabalha em segundo plano e encerra após cerca de
dois minutos sem uso; bridges de mídia são mantidas brevemente para retomadas.
O túnel não altera a rota padrão, o DNS nem o proxy global do sistema.

Para remover, encerre o Discord e use **Desinstalar** no instalador. No Linux,
execute `python3 ~/.local/share/discord-stream-tunnel/launcher.py uninstall`.
Abra o Discord uma vez para restaurar a preferência anterior de vídeo.
O `.conf` original em Downloads permanece onde você o salvou.

Dados privados ficam em `%LOCALAPPDATA%/DiscordStreamTunnel/data` no Windows
ou `$XDG_DATA_HOME/discord-stream-tunnel/data` (padrão `~/.local/share`) no Linux.
**Não compartilhe a pasta instalada ou arquivos `.conf`.** Releases e o código
público não contêm configurações de conta, tokens, cookies ou chaves privadas.

## Verificar / desenvolver

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=source:source/vendor python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_runtime.cjs
node tests/test_stream_startup.cjs
node tests/test_native.cjs
node tests/test_helper_recovery.cjs
node tests/test_updates.cjs
node tests/test_sign_release.cjs
node tests/test_update_notice.cjs
```

`scripts/build_windows.py` usa PyInstaller. `scripts/package_release.py`
gera os pacotes e o bundle de atualização. `scripts/sign_release.cjs` assina
com uma chave de publicação mantida **fora do repositório**. A chave privada
nunca acompanha a release; `source/trust.json` contém apenas a chave pública.
Atualizações distribuídas precisam ser assinadas pela chave correspondente.

Para publicar, preserve a chave existente e baixe `update-manifest.json` da
release anterior para uma pasta de publicação fora do repositório. Depois de
gerar os pacotes, execute:

```sh
node scripts/sign_release.cjs /pasta-privada/release-signing-private.pem --compat-manifest /pasta-privada/update-manifest-anterior.json
```

O script valida a assinatura anterior e deriva dela o endereço de
compatibilidade. Publique **ambos** os manifestos, `runtime-bundle.json.gz` e
os pacotes Windows/Linux na nova release, marcada como a mais recente. Não
troque a chave nem publique apenas um dos manifestos: instalações antigas
dependem desse caminho de migração. Commits, tags e releases anteriores são
preservados como histórico do projeto.

O núcleo sing-box não é redistribuído: o instalador baixa a versão oficial
1.14.3 e confere os hashes fixados em `source/core-download.json`.
[Código e licença do núcleo](https://github.com/SagerNet/sing-box/tree/v1.14.3).
As licenças das bibliotecas Python estão em `source/vendor`.

O projeto usa APIs internas do Discord/Vencord e pode precisar de manutenção.
Recepção real da versão 1.1.0 foi confirmada em Windows com Vencord b0e636d,
vídeo 1920×1080 a 60 FPS e quadros avançando sem erros de decodificação na amostra.
O backend e o transporte foram testados no Ubuntu/WSL, mas a imagem no Discord
em desktop Linux ainda precisa de validação. Testes de transporte
ou recepção não comprovam o envio da própria tela. O projeto é independente
de Discord, Vencord, Proton e sing-box.
