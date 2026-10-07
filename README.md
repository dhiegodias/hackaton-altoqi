# Busqi · Hackathon AltoQi

O Busqi ajuda o comercial a encontrar novos contatos, completar a base com informações de fontes públicas e revisar cada alteração antes de enviá-la à HubSpot. A captura em campo funciona no celular, inclusive offline.

## Rodar em um comando

**Pré-requisito:** Docker com Docker Compose **2.24 ou superior** — incluído nas versões recentes do Docker Desktop. Não é necessário instalar Python, Node.js ou PostgreSQL na máquina. A primeira execução precisa de internet para baixar imagens e dependências.

Clone o repositório e entre na pasta:

```bash
git clone git@github.com:dhiegodias/hackaton-altoqi.git
cd hackaton-altoqi
```

Execute:

```bash
docker compose up --build -d --wait
```

Abra **[http://localhost:8787](http://localhost:8787)**. A captura pelo celular está em **[http://localhost:8787/campo](http://localhost:8787/campo)**.

O Compose prepara a configuração, inicia PostgreSQL, aplicação e dois workers, cria as tabelas e carrega **oito contatos fictícios** na primeira execução. A senha local e a chave que protege os bloqueios de reimportação são geradas automaticamente em um volume Docker. **Não precisa criar `.env` nem configurar IA ou HubSpot para experimentar a demonstração.**

O serviço `bootstrap` terminar com código `0` é esperado: ele prepara as chaves e encerra. Os demais serviços permanecem rodando. Se a porta 8787 já estiver em uso, copie `.env.example` para `.env`, altere `RADAR_PORT` e execute o mesmo comando.

## Primeiro uso

1. Abra **Radar da base** para explorar os contatos e sugestões fictícias.
2. Use **Importar lista** para enviar um CSV ou XLSX e conferir o mapeamento das colunas. Acompanhe progresso e erros em **Listas e exportações**.
3. Ative as fontes em **Fontes e regras** para pesquisar contatos reais. Elas começam desligadas.
4. Confira valores, confiança e evidências em **Revisão**; aprove, edite com justificativa ou rejeite cada sugestão.
5. Em **Captura em campo**, registre a conversa e o contexto comercial. Em **Prospecção outbound**, pesquise um contato antes de adicioná-lo à base local.
6. Prepare a prévia de envio. A simulação serve para a apresentação; escrever na HubSpot exige a configuração descrita abaixo.

**Demonstração, pesquisa pública, aprovação local, simulação e envio real são operações diferentes. Dados fictícios nunca são enviados à HubSpot.**

## Configuração opcional

Para personalizar o ambiente:

```bash
cp .env.example .env
```

Edite `.env` e reaplique com `docker compose up --build -d --wait`. O arquivo contém todas as opções suportadas para a execução pelo Compose; mantenha credenciais apenas nele. Sem configuração, a aplicação fica disponível somente na própria máquina, em modo demonstração.

| Variável | Uso / padrão |
| --- | --- |
| `RADAR_PORT` / `RADAR_BIND` | Porta `8787` e endereço `127.0.0.1`. |
| `RADAR_IMAGE` | Nome da imagem local, `radar-crm:local`. Útil para ambientes separados. |
| `POSTGRES_PASSWORD` | Opcional na primeira execução; vazia gera senha aleatória persistida. |
| `RADAR_ERASURE_KEY` | Opcional na primeira execução; vazia gera chave persistida. Valor manual exige ao menos 32 caracteres. |
| `DEMO_MODE` | `true`: carrega dados fictícios em uma base nova e permite uso local sem login. |
| `RADAR_ACCESS_TOKEN` | Chave de acesso da equipe, obrigatória quando `DEMO_MODE=false`. |
| `COOKIE_SECURE` | `false` no HTTP local; `true` ao usar HTTPS. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | Pesquisa e interpretação por IA opcionais. |
| `HUBSPOT_ACCESS_TOKEN` | Token do portal para importar contatos e, se habilitado, enviar alterações. |
| `HUBSPOT_WRITE_ENABLED` | `false`: impede escrita real mesmo com token configurado. |
| `HUBSPOT_FIELD_MAP` | JSON com o mapeamento entre campos locais e propriedades de contato do portal. |
| `RADAR_SCAN_INTERVAL_SECONDS` | `0`: sem pesquisa periódica; `86400`: intervalo de um dia. |
| `RADAR_IMPORT_MAX_ROWS` | Limite por arquivo, `100000` linhas. |
| `RADAR_IMPORT_MAX_BYTES` | Limite por arquivo, `104857600` bytes (100 MiB). |

`DATABASE_URL` é montada automaticamente dentro dos containers para o PostgreSQL deste Compose. Não precisa ser preenchida.

**Preserve os volumes `radar_data` e `radar_secrets` juntos.** Alterar uma senha ou chave já persistida no `.env` não rotaciona uma base existente: a inicialização rejeita valores diferentes. A chave de eliminação precisa permanecer estável para reconhecer cadastros bloqueados contra reimportação. Configurações de uma instalação local anterior são adotadas quando o volume de segredos é criado pela primeira vez.

### Pesquisa e IA

A pesquisa pública usa BrasilAPI para CNPJ numérico, IBGE para cidade/UF e páginas públicas permitidas conforme as regras de cada campo. Cargo não comprova poder de decisão; fit é uma hipótese comercial explicada. As listas de cargos, segmentos, fontes permitidas, pesos e limiares podem ser configuradas na interface.

Sem credenciais OpenAI, a pesquisa usa os conectores públicos e regras implementadas. Para a etapa opcional de IA, configure chave e modelo compatível com **Responses, web search e saída estruturada**, e habilite as opções na interface. Os textos e identificadores necessários à pesquisa são enviados ao provedor quando essa opção está ativa. A aplicação não raspa LinkedIn ou Instagram; links pessoais e empresariais são tratados separadamente.

### HubSpot

Para importar, configure `HUBSPOT_ACCESS_TOKEN` com permissão de leitura de contatos. A importação percorre páginas na fila de jobs.

Para enviar alterações reais, configure também permissão de escrita, `HUBSPOT_WRITE_ENABLED=true` e o mapeamento das propriedades de **contato** já existentes no seu portal. O mapeamento inicial contempla `website` e `jobtitle`; acrescente os demais campos conforme as propriedades do portal. O contato deve possuir `hubspot_id` válido.

O envio exige campos aprovados, evidências vigentes, prévia e confirmação. Conflitos com valores remotos bloqueiam a operação. Valores vazios não apagam o CRM, e o resultado só é confirmado após releitura remota. O adapter não cria contatos, empresas ou associações na HubSpot. A prospecção outbound cria contatos apenas no Busqi, após conferência.

### Captura no celular

A rota `/campo` pode ser instalada como PWA. Abra conectado uma vez e aguarde **Acesso offline preparado**. Depois, rascunhos e registros pendentes permanecem no navegador; a fila envia quando a conexão retorna com o aplicativo aberto ou na próxima abertura. Envio com o aplicativo fechado não é garantido.

Para outros celulares acessarem, configure endereço acessível, chave da equipe e **HTTPS**; `localhost` aponta para o próprio aparelho. Instalação e reabertura offline normalmente exigem HTTPS, exceto no localhost. O Compose inclui HTTP local; não provisiona domínio ou certificado. Não limpe os dados do navegador enquanto houver registros pendentes.

## Operação e limites

- **Listas:** CSV UTF-8/Windows-1252 e XLSX sem fórmulas; até 100 mil linhas por padrão. Processamento em lotes, progresso, erros por linha, cancelamento e recuperação após interrupção. Arquivos recebidos expiram em 24 horas. Feche a aba somente depois da confirmação do upload.
- **Exportação:** CSV gerado em segundo plano, apenas com campos aprovados e elegíveis. O arquivo expira em até 30 minutos e é invalidado quando sua base muda.
- **Fila:** PostgreSQL persistente, com workers Python separados para pesquisa e transferências. Não utiliza Celery ou Redis.
- **Confiança:** pontuação explicável com fonte e data. O score não representa probabilidade estatística de acerto. Backfill automático preenche apenas lacunas locais elegíveis quando habilitado; não escreve na HubSpot.
- **Auditoria e privacidade:** histórico paginado, supressão de contatos e eliminação local com confirmação e bloqueio de reimportação. A eliminação não apaga cópias já baixadas, backups ou dados na HubSpot. Não representa adequação jurídica completa à LGPD.
- **Acesso:** token compartilhado da equipe; não há usuários individuais, RBAC ou SSO. Este é um protótipo para o hackathon interno.

As regras comerciais se baseiam nos playbooks de [Escritórios de Projeto](https://solution-playbook.vercel.app/escritorios/index.html) e [Gestão da Construção](https://solutionplaybookgestaodaconstrucao.netlify.app/). A classificação é um apoio ao diagnóstico comercial: funcionários não substituem projetistas, capital social não estima equipe e dados ausentes não viram falso ou zero.

## Comandos úteis

```bash
# Estado dos serviços e logs (sem expor valores de .env)
docker compose ps -a
docker compose logs -f app worker transfer-worker

# Parar preservando os dados e as chaves
docker compose stop

# Reiniciar ou aplicar alterações de código/configuração
docker compose up --build -d --wait
```

Os logs usam saída padrão dos containers. Não há plataforma de logs centralizada. O banco não publica porta no host. O código é copiado para a imagem; mudanças exigem rebuild.

## Desenvolvimento e testes

```bash
docker compose build app
docker compose run --rm app ruff format --check radar tests
docker compose run --rm app ruff check radar tests
docker compose run --rm app pytest
```

A suíte usa schemas PostgreSQL isolados e um destino HTTP de teste com estado para HubSpot; não escreve em um portal real. Cobre revisão, conflitos, fontes vencidas, exclusão, concorrência, reenvio, importação, recuperação da fila e configuração inicial. Nunca use um banco de produção para esses testes.

| Caminho | Conteúdo |
| --- | --- |
| `radar/` | API FastAPI, regras comerciais, conectores, persistência e workers. |
| `static/` | Interface Busqi, PWA, logo e fonte local com licença. |
| `tests/` | Testes e fixtures necessárias à sua execução. |
| `compose.yaml` / `Dockerfile` | Inicialização completa pelo Docker. |
| `.env.example` | Opções documentadas sem credenciais. |

Materiais de pesquisa, vídeos, relatórios e evidências de desenvolvimento ficam fora do repositório e não são necessários para executar a aplicação. O logo utilizado pela interface está incluído nos próprios ativos de `static/`.
