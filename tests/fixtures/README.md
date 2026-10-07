# Fixtures de aceitação

- `importacao_100.xlsx`: Excel efetivamente importado pelo navegador e pela API. Uma aba, 100 pessoas fictícias e seis colunas. Nomes com acentos, valores numéricos incluindo zero, domínios `.invalid`. Não contém colaboradores reais da AltoQi.
- `importacao_100.json`: dados independentes de origem usados para conferir todos os 600 valores persistidos. Criados pelo mesmo gerador de massa, sem chamar código do Radar.
- `altoqi_public.json`: recorte corporativo público coletado em 03/10/2026. Inclui fonte, data e evidência. Não inclui dados de sócios, empregados, e-mails reais ou credenciais. Site/redes/CNPJ foram conferidos manualmente; capital, CNAE, porte e localização vieram do coletor BrasilAPI.

O XLSX foi criado com a ferramenta de planilhas e é um artefato intencional de teste. A suíte exercita o adapter HTTP real contra um servidor local com estado, dentro do Docker. Não escreve no portal HubSpot.

Os nomes de propriedades customizadas e o ID de contato são ilustrativos. O recorte público fixo serve à repetibilidade dos testes; não é uma garantia de atualidade futura do cadastro.


`altoqi_backfill_public.json` é o recorte da coleta pública de 03/10/2026: Maria Porfiro aparece como Marketing Specialist na página de carreiras; a institucional informa mais de 300 colaboradores. As propostas incluem hipóteses marcadas, não alçada ou aderência de compra confirmadas. Nenhum e-mail pessoal foi criado ou coletado. O recorte sustenta a reprodução do contrato HTTP em ambiente de teste; não renova as fontes por si mesmo.
