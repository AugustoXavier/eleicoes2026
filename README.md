# Eleicoes_2026

Painel web para acompanhar a apuração das eleições de 2026 com dados oficiais do Tribunal Superior Eleitoral (TSE).

## Estrutura

- `src/eleicoes_2026/` - servidor HTTP local e integração com a API do TSE
- `static/index.html` - painel responsivo com atualização automática
- `tests/` - testes da interpretação da configuração e dos resultados

## Executar

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
python -m eleicoes_2026.main
```

Abra [http://127.0.0.1:8000](http://127.0.0.1:8000) no navegador e selecione a eleição, o cargo e o estado. O campo de cidade permite filtrar a apuração para um município; deixe **Todas as cidades do estado** selecionado para ver o total estadual. O painel atualiza os resultados a cada 30 segundos; o botão **Atualizar agora** permite consultar antes.

## Publicar na internet com Render

O repositório inclui `render.yaml` para criar um serviço web gratuito no Render:

1. Entre em [render.com](https://render.com/) e escolha **New > Blueprint**.
2. Conecte sua conta GitHub e selecione `AugustoXavier/eleicoes2026`.
3. Revise o serviço `eleicoes2026` e escolha **Apply**.
4. Quando o serviço ficar com status **Live**, abra a URL `https://eleicoes2026.onrender.com` exibida no painel do Render e compartilhe-a.

O Render instala o projeto e inicia o servidor usando a porta fornecida pela plataforma. O painel é público e não exige login; qualquer pessoa com o endereço poderá acessá-lo. No plano gratuito, o serviço pode suspender quando ficar inativo e demorar alguns instantes para responder no primeiro acesso.

Use o filtro **Legenda** para exibir somente os candidatos do partido selecionado. Os totais de votos e de seções continuam representando toda a localidade, mesmo quando a lista de candidatos está filtrada.

Os candidatos que aparecem dentro das vagas estimadas são destacados em verde. Para presidente e governador, se ninguém ultrapassar 50% dos votos válidos, o destaque indica os dois mais votados, que disputariam o segundo turno; se alguém ultrapassar a maioria absoluta, o painel destaca esse candidato. Votos brancos e nulos não entram no cálculo. Esses destaques sempre usam o resultado nacional para presidente e estadual para governador, mesmo se a lista de votos estiver filtrada por cidade. Para senador e para deputado federal, estadual e distrital, o destaque considera o número de vagas e, nos cargos proporcionais, as vagas atribuídas pelo TSE a cada legenda, a votação individual e o mínimo legal de votos. As indicações são estimativas durante a apuração, não uma declaração de resultado final.

Quando o TSE marcar oficialmente uma candidatura como eleita nos dados de apuração, o painel destaca o candidato com o selo **ELEITO PELO TSE**. Esse status oficial tem precedência sobre as estimativas de vagas e segundo turno; se o TSE ainda não tiver publicado a marcação, o painel não presume que alguém foi eleito.

Fontes legais da regra de segundo turno: [Constituição Federal, art. 28 e art. 77, §§ 2º a 5º](https://www.planalto.gov.br/ccivil_03/constituicao/constituicaocompilado.htm#art77). O governador é eleito por maioria absoluta de votos válidos; se nenhum candidato a alcançar no primeiro turno, os dois mais votados disputam o segundo. Em caso de empate na classificação, aplica-se o critério de idade previsto no art. 77, § 5º.

O painel permite escolher eleição, cargo, estado e município. Inclui presidente, governador, senador, deputado federal, deputado estadual e, no Distrito Federal, deputado distrital. A apuração municipal mostra a votação da localidade selecionada para os cargos em disputa naquele pleito.

O resumo da apuração também mostra os votos válidos, computados, brancos e nulos divulgados pelo TSE para a localidade selecionada.

Quando publicada pelo TSE, a foto oficial do candidato é exibida ao lado do nome. Se a imagem estiver indisponível, o painel mostra as iniciais do candidato.

O servidor consulta a configuração pública em `https://resultados.tse.jus.br/oficial/comum/config/ele-c.jws`, a lista oficial de municípios e os arquivos de apuração do TSE. Se a apuração ainda não tiver começado, o painel informa que nenhuma seção foi totalizada. A disponibilidade e a frequência de publicação dependem do TSE.

As urnas fecham às 17h, no horário de Brasília. A totalização fica visível conforme o TSE recebe e divulga os resultados.

Para executar os testes:

```bash
python -m unittest discover -s tests
```
