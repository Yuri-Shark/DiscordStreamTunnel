# Segurança e assinatura

## Aviso “Windows protected your PC”

Esse aviso é emitido pelo SmartScreen quando a reputação do arquivo ou do
publicador é desconhecida. Ele é diferente de uma detecção de malware pelo
Microsoft Defender. Alterar ícone, nome ou metadados não elimina o aviso.

**Esta versão ainda não tem assinatura Authenticode reconhecida pelo Windows.**
O nome Yuri-Shark nos detalhes do arquivo identifica o projeto; não significa
que o Windows verificou a identidade do publicador. Certificados autoassinados
também não criam confiança pública no SmartScreen.

O instalador roda com as permissões do usuário, usa a API nativa do Windows
para atalhos e não inicia PowerShell. O pacote mantém a distribuição em pasta,
sem UPX nem extração de executável único para diretórios temporários.
Essas escolhas simplificam a instalação e a auditoria; não garantem redução
de alertas. O programa não adiciona exclusões nem desativa proteções.

## Conferir os downloads

Baixe somente da [release do projeto](https://github.com/Yuri-Shark/DiscordStreamTunnel/releases/latest).
Cada release acompanha `SHA256SUMS.txt`. Para conferir um ZIP no PowerShell:

```powershell
Get-FileHash .\DiscordStreamTunnel-1.2.0-Windows-x64.zip -Algorithm SHA256
```

Compare o resultado com o arquivo de hashes da mesma release. Os atualizadores
verificam a assinatura Ed25519 e o SHA256 do pacote; o da integração também
verifica cada arquivo. A assinatura protege os downloads do projeto e é
independente da assinatura Authenticode usada pelo SmartScreen.

O instalador Windows consulta o manifesto assinado ao abrir. A janela de
atualização só aparece para uma versão superior e oferece **Agora não** e
**Atualizar e reabrir**. O download e a execução dependem desse clique.
O ZIP é verificado antes de extrair; caminhos inseguros, links, arquivos
duplicados e tamanhos excessivos são rejeitados. Uma atualização aceita fica
em `%LOCALAPPDATA%/DiscordStreamTunnelInstaller`. Ao abrir novamente o pacote
antigo, o cache é revalidado antes de encaminhar para a versão nova. Não há
script para sobrescrever um executável em uso.

## Code signing policy

A versão 1.4.0 recupera a publicação após perda da chave Ed25519 anterior.
Os clientes anteriores não reconhecem a nova chave: é necessário baixar o
novo instalador e clicar em Reparar uma vez (Linux: `python3 source/app.py repair`
no pacote novo). Nenhum cliente aceita assinaturas inválidas para contornar
essa migração. A partir da 1.4.0, uma assinatura não reconhecida gera um aviso
para consultar manualmente a release oficial; o pacote permanece bloqueado.

A nova chave privada fica criptografada por Windows DPAPI no perfil do usuário,
fora do repositório, com permissões limitadas ao usuário e SYSTEM. Uma cópia
fica no secret de Actions `DTS_RELEASE_SIGNING_KEY`. O script
`scripts/publisher_key.ps1` envia a chave por stdin e assina sem criar um PEM
em disco. O workflow de release recebe a chave apenas no passo de assinatura.
DPAPI não é um backup portátil: a troca/perda do perfil Windows pode impedir
a recuperação. Secrets do GitHub não podem ser exportados pela interface.

Responsável pelo código, revisão e aprovação de releases: **Yuri-Shark**.
A chave Ed25519 de publicação permanece fora do repositório e dos pacotes.
Nunca publique configurações WireGuard, chaves privadas ou autorizações locais.

Para reduzir os avisos por falta de reputação, uma futura publicação deve usar
um certificado Authenticode válido, com carimbo de tempo e a mesma identidade
entre versões. Mesmo arquivos assinados podem mostrar avisos enquanto sua
reputação é construída.

Uma opção gratuita para projetos elegíveis é a
[SignPath Foundation](https://signpath.org/). Ela exige análise do projeto,
controle verificável do build e aprovação das solicitações de assinatura.
O projeto **não foi inscrito nem aprovado**; os arquivos atuais não são
assinados pela SignPath. A elegibilidade depende dos
[termos da fundação](https://signpath.org/terms).

Assine os executáveis antes de empacotar e gerar hashes. Não altere os
binários após a assinatura. Valide `Get-AuthenticodeSignature`, gere os pacotes
e publique ambos os manifestos usando a chave Ed25519 existente.

## Dados e rede

Não há telemetria do projeto. A instalação baixa o núcleo oficial do GitHub,
e os atualizadores consultam releases públicas. O túnel usa a configuração
Proton escolhida pelo usuário para o controle Gateway e as conexões de mídia
de compartilhamento do Discord. O Gateway inclui eventos de mensagens, presença
e chamadas; o auxiliar encaminha seus frames sem interpretar ou guardar o
conteúdo. A voz principal e HTTP/CDN continuam diretos. O serviço local exige
a origem do Discord e autorização privada, e restringe hosts, portas, parâmetros
e conexões simultâneas; não altera as rotas ou o DNS do sistema.
Configurações e diagnósticos locais ficam na pasta privada da instalação;
chaves e conteúdo de transmissões não entram em logs públicos.

Referências:

- [Reputação do SmartScreen para desenvolvedores](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation).
- [Opções de assinatura para Windows](https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options).
- [Análise de arquivos pela Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission), para detecções concretas de malware/PUA; não é um cadastro automático de reputação do SmartScreen.
