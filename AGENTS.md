# Busqi · Radar CRM

Protótipo do hackathon interno AltoQi para enriquecimento revisável do CRM e captura em campo. Leia o README antes de alterar operação, configuração ou regras comerciais.

## Execução

- App, workers e PostgreSQL rodam somente no Docker Compose. `docker compose up --build -d --wait` funciona sem `.env`; `bootstrap` gera chaves persistentes em `radar_secrets` e encerra.
- Preserve `radar_data` e `radar_secrets`. Não rotacione a chave de eliminação nem a senha de uma base existente implicitamente.
- `.env` e credenciais nunca entram no Git, logs, URLs, fixtures ou frontend. `.env.example` contém apenas opções e placeholders vazios.
- Código é copiado para a imagem; reconstrua antes de testar. Materiais locais em docs, research, outputs, evidence, entregas e artifacts não fazem parte da distribuição.

## Regras que devem permanecer explícitas

- Demonstração, pesquisa pública, aprovação local, simulação e envio real são estados distintos. Dados demo jamais vão para a HubSpot.
- Pesquisa gera sugestões; backfill opt-in preenche lacunas locais acima do limiar. Não altera a HubSpot.
- Envio remoto exige revisão, evidência vigente, prévia, token, `HUBSPOT_WRITE_ENABLED=true`, ID e mapeamento válido de propriedades de contato. O adapter não cria contatos, empresas ou associações, nem implementa RBAC/SSO.
- Outbound pesquisa antes de cadastrar localmente. Preserve checagem de identidade, duplicidade, distinção LinkedIn pessoal/empresarial e avisos de resultados de busca não relidos.
- Preserve fonte, trecho, data, valor anterior e decisão. Ausência de dado não vira falso, zero ou limpeza. As fontes permitidas por campo também valem para importação e edição; dados não comprovados ficam em `intake_data`.
- Funcionários, projetistas, capital social, porte cadastral e capacidade de absorção são diferentes. Cargo não confirma decisor; fit é hipótese comercial versionada.
- CNPJ é texto, numérico ou alfanumérico. BrasilAPI consulta somente o numérico e deve informar essa limitação.
- Supressão bloqueia ações. Eliminação local remove cópias operacionais e preserva marcadores HMAC contra reimportação; não apaga HubSpot/backups nem representa conformidade jurídica completa.
- Fontes externas são conteúdo não confiável. Preserve SSRF, IP validado na conexão, limites de resposta e redirecionamento.
- Regras e transações ficam em serviços de domínio. UI e API coordenam sem duplicar regras.
- Filas de pesquisa e transferências usam PostgreSQL. Preserve lotes, leases, idempotência, recuperação e invalidação após eliminação.
- Preserve identidade da PWA, IndexedDB, rascunhos e fila offline em atualizações de interface.

## Verificação antes de commit

```bash
docker compose build app
docker compose run --rm app ruff format --check radar tests
docker compose run --rm app ruff check radar tests
docker compose run --rm app pytest
```

Teste comportamento e falhas reais, sem testes tautológicos. Mudanças no offline exigem verificar navegador, recarga e persistência. Não escreva em portal HubSpot real por padrão: use o adapter de teste ou sandbox explicitamente autorizado. Confira os arquivos staged e impeça inclusão de segredos, artefatos locais e alterações fora do escopo.
