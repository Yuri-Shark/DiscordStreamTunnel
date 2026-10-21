# DiscordStreamTunnel

Integração para encaminhar as sessões de compartilhamento de tela do Discord
por um túnel Proton WireGuard em userspace. A voz principal da chamada e os
demais aplicativos continuam pela conexão normal.

## Instalar

Baixe os pacotes em [Releases](https://github.com/eduardofhabib/DiscordStreamTunnel/releases/latest).
É necessário Discord desktop nativo com Vencord e um `.conf` WireGuard
da **própria conta Proton**. O portal permite gerar configurações no plano gratuito:
[instruções oficiais](https://protonvpn.com/support/wireguard-configurations).

**Windows x64:** extraia o ZIP inteiro, encerre o Discord, execute
`DiscordStreamTunnel.exe`, escolha seu `.conf` e clique em **Instalar / reaplicar**.
O programa inclui Python. Depois abra o Discord pelo atalho normal.

**Linux:** use Discord nativo (`pacman`, `.deb`, pacote da distribuição) e Vencord.
É necessário Python 3.10 ou superior; as bibliotecas já estão incluídas.
Flatpak e Snap não são cobertos.

```sh
tar -xzf DiscordStreamTunnel-1.1.1-linux.tar.gz
cd DiscordStreamTunnel-1.1.1-linux
sh instalar.sh
```

No CachyOS/Arch, se faltar Python: `sudo pacman -S --needed python ca-certificates`.
No Ubuntu: `sudo apt install python3 ca-certificates`. Execute a integração
com seu usuário normal. Instale Vencord pelas [instruções oficiais](https://vencord.dev/download/).

## O que mudou na 1.1.0

A versão 1.1.1 move o endereço canônico das atualizações para a conta pessoal
`eduardofhabib`. A chave de assinatura foi preservada. Uma release de migração
mantém o download compatível com clientes 1.1.0 usando o redirecionamento do
GitHub; versões futuras consultam diretamente o repositório pessoal.

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

Uma atualização baixada em segundo plano fica ativa **na próxima abertura do
Discord**, para preservar as chamadas e transmissões em andamento. Não há
reinício forçado. O estado fica em `data/update-status.json`.

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
node tests/test_updates.cjs
```

`scripts/build_windows.py` usa PyInstaller. `scripts/package_release.py`
gera os pacotes e o bundle de atualização. `scripts/sign_release.cjs` assina
com uma chave de publicação mantida **fora do repositório**. A chave privada
nunca acompanha a release; `source/trust.json` contém apenas a chave pública.
Atualizações distribuídas precisam ser assinadas pela chave correspondente.

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
