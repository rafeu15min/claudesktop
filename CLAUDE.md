# claudesktop — regras do projeto

- **YAGNI**: não construir nada da lista "fora do escopo do v1" (README) até
  surgir necessidade real e confirmada com o usuário. Isso inclui a captura de
  debugging de teclado físico do plano original — é um recurso maior e sensível
  (lê tudo que o usuário digita, sem filtro de janela), não um extra trivial.
- **Screenshot pós-ação é deliberado, não remover pra economizar tokens sem
  perguntar.** Toda ferramenta que muda estado (`move_mouse`, `move_to_window`,
  `click`, `scroll`, `type_text`, `send_keys`) devolve um screenshot novo —
  é a única forma do agente confirmar o efeito antes da próxima ação.
- **`check_environment()` (e `hyprvision doctor`) têm que continuar só-leitura**
  — nunca mover mouse, nunca digitar, nunca mudar nada. É a ferramenta chamada
  no início de qualquer sessão.
- Nunca assumir a coordenada de tela como uma constante do código — sempre ler
  de `hyprctl` (`geometry.py`/`hyprctl.py`), inclusive `scale` do monitor, mesmo
  que hoje só exista uma tela com `scale: 1` nesta máquina.
- `src/claudesktop/env.py` (`ensure_session_env`) precisa continuar sendo
  chamado antes de qualquer uso de `hyprctl`/`grim` — hosts de MCP não repassam
  `HYPRLAND_INSTANCE_SIGNATURE`/`WAYLAND_DISPLAY` por padrão. Não substituir
  isso por um valor fixo capturado uma vez (fica obsoleto a cada reboot).
- Se algum dia implementar a captura de debugging de teclado: nunca colar o log
  bruto na conversa; se algo capturado parecer senha/segredo (texto logo após
  um prompt de senha/sudo/login), sinalizar que algo sensível foi percebido em
  vez de reproduzir o valor — mesmo que a resposta fique menos completa.
- **Ordem de preferência de ferramenta (2026-09-05/06):** pra qualquer coisa
  dentro de um app GTK/Qt nativo, usar `atspi_*` antes de `click`/`screenshot`
  às cegas; pra qualquer coisa dentro de uma página em navegador Chromium,
  usar `browser_*` antes disso. Print+clique continua existindo e é o método
  certo pra jogos/conteúdo puramente visual (canvas sem estrutura nenhuma pra
  consultar) — não é "menos bom", é a ferramenta certa pra outra categoria de
  alvo. Não usar o print+clique por padrão só porque é o caminho mais antigo
  do projeto.
- **AT-SPI é D-Bus puro via `jeepney`, não PyGObject.** `gi.repository.Atspi`
  funciona no Python do sistema (3.14) mas não no venv do `uv` (3.13) — ABI de
  extensão compilada incompatível, `--system-site-packages` não resolve. Não
  tentar "consertar" isso trocando pra PyGObject sem antes alinhar a versão do
  Python do projeto com a do sistema (e mesmo assim, isso reintroduziria uma
  dependência de sistema pesada que o `jeepney` evita).
- Cobertura do AT-SPI é parcial por natureza, não um bug a corrigir: Chromium/
  Vivaldi e apps que renderizam tudo num canvas (Alacritty, jogos) nunca vão
  aparecer em `atspi_apps()` — isso é esperado, não sinal de que o módulo está
  quebrado.
