# claudesktop

Servidor MCP local (Python) que expõe controle de mouse/teclado com consciência de
geometria de janela para o Claude Code, no lugar da gambiarra de comandos `bash`
soltos com coordenadas chutadas a partir de um print.

Ferramentas: listar janelas, screenshot (tela cheia/monitor/região), mover mouse
(absoluto ou até uma janela por âncora), clicar, scroll, digitar texto, teclas de
atalho, e um diagnóstico só-leitura (`check_environment`). Toda ferramenta que
muda estado devolve um screenshot novo junto da resposta — é assim que o agente
(sem percepção contínua da tela) confirma o efeito de cada ação.

Além disso, duas famílias de ferramentas *estruturadas* (2026-09-05/06), que
localizam elementos por nome/papel/DOM em vez de pixel — print+clique vira
método contingente, não o padrão:

- **`atspi_*`** — árvore de acessibilidade (AT-SPI) para apps GTK/Qt nativos.
- **`browser_*`** — DOM via Chrome DevTools Protocol para navegadores baseados
  em Chromium (Vivaldi, Chrome, Brave...).

Nenhuma das duas cobre tudo: AT-SPI não vê Chromium/Vivaldi nem terminais
renderizados por GPU (Alacritty), e CDP só cobre o que está dentro da página —
para esses casos, e pra jogos/conteúdo puramente visual, print+clique continua
sendo o método real (não tem estrutura nenhuma pra consultar num canvas
OpenGL/Vulkan).

## Como funciona

`ydotool` injeta eventos via `/dev/uinput` (um dispositivo de mouse/teclado
*virtual*, agindo direto no compositor real — não numa tela virtual ou sandbox).
Qualquer ação já acontece na tela física em tempo real; quem está na frente do
monitor vê tudo ao vivo, sem precisar de streaming ou segundo monitor.

## Configuração de sistema (uma vez, precisa de sudo)

```bash
sudo pacman -S ydotool

sudo tee /etc/udev/rules.d/60-ydotool-uinput.rules <<'EOF'
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger /dev/uinput

sudo usermod -aG input "$USER"
# precisa relogar na sessão pra valer
```

O pacote `ydotool` já traz a unit `ydotool.service` (usuário, não root/sistema) —
não é preciso criar uma manualmente:

```bash
systemctl --user enable --now ydotool.service
systemctl --user status ydotool.service
```

## Instalação do projeto

```bash
cd /mnt/Utilitarios/Projetos/claudesktop
uv sync
uv run claudesktop doctor   # confirma ydotoold/grim/hyprctl/uinput todos ok
```

## Registro no Claude Code

```bash
claude mcp add claudesktop --scope user -- uv run --project /mnt/Utilitarios/Projetos/claudesktop claudesktop serve
claude mcp list
claude mcp get claudesktop
```

`--scope user`: fica disponível em qualquer sessão futura do Claude Code nesta
máquina, não só num repositório. **Isso é um salto de confiança bem maior que
ferramentas de leitura/edição de código** — qualquer sessão futura poderá mover
mouse e injetar teclado.

O host do MCP normalmente só repassa um subconjunto mínimo de variáveis de
ambiente pro processo do servidor (não `HYPRLAND_INSTANCE_SIGNATURE` nem
`WAYLAND_DISPLAY`). `src/claudesktop/env.py` descobre esses valores sozinho a
partir de `$XDG_RUNTIME_DIR` a cada `serve`/`doctor`/`panic` — funciona mesmo
depois de um reboot, sem precisar re-registrar nada.

## Kill switch

```bash
uv run claudesktop panic
```

Para o `ydotoold` na hora (via systemd), independente do estado do servidor MCP
— funciona mesmo que uma chamada de ferramenta esteja travada.

Atalhos globais no Hyprland (`~/.config/hypr/hyprland.lua`):

- `SUPER + SHIFT + P` → `claudesktop panic` (desliga o `ydotoold`)
- `SUPER + P` → religa o `ydotoold` (`systemctl --user restart ydotool.service`)

O powermenu (wlogout), que antes estava em `SUPER + P`, foi movido para
`SUPER + X` para abrir espaço para esses dois.

## Prioridade do usuário físico

Você sempre tem prioridade sobre qualquer ação sintética. Como o `ydotool` usa
`uinput` (dispositivo virtual, independente do mouse/teclado real), essa
prioridade não é arbitrada em hardware — o kill switch acima é a camada de
software que garante isso hoje. Ainda não implementado: detecção automática de
input físico real durante uma ação sintética (ver plano original — só vale a
pena se a investigação mostrar que é simples).

## Superfície de ferramentas MCP (v1)

- `list_windows()`
- `screenshot(monitor=None, region=None)`
- `move_mouse(x, y, monitor=None)`
- `move_to_window(address, anchor="center"|"top-left"|"titlebar", offset=[0,0])`
- `click(button="left", double=False, x=None, y=None)`
- `scroll(dx=0, dy=0)`
- `type_text(text)`
- `send_keys(keys)` — ex. `"ctrl+shift+t"`
- `check_environment()` — só-leitura, seguro de chamar a qualquer momento

**AT-SPI** (apps GTK/Qt nativos — ver `atspi_apps()` pra saber quais estão
rodando):

- `atspi_apps()` — só-leitura
- `atspi_tree(app, max_depth=6, max_children=40)` — só-leitura
- `atspi_find(app, name=None, name_contains=None, role=None)` — só-leitura
- `atspi_click(app, name=None, name_contains=None, role=None, index=0, via="mouse"|"action")`

**CDP** (navegadores Chromium — exige o navegador já rodando com
`--remote-debugging-port=9222`; ver seção própria abaixo):

- `browser_targets(port=9222)` — só-leitura
- `browser_find(selector=None, text_contains=None, target_index=0, port=9222)` — só-leitura
- `browser_click(mark, target_index=0, port=9222)`
- `browser_type(mark, text, target_index=0, port=9222)`
- `browser_get_text(selector="body", target_index=0, port=9222)` — só-leitura
- `browser_navigate(url, target_index=0, port=9222)`

Fora do escopo do v1 (não construído especulativamente): drag-and-drop,
multi-touch, clipboard, mover/fechar janelas, OCR, captura contínua/vídeo, e a
captura de debugging de teclado físico descrita no plano original (recurso
maior e sensível — ver plano antes de implementar).

## AT-SPI: por que D-Bus puro em vez de PyGObject

`gi.repository.Atspi` já funciona contra o Python do *sistema* (3.14) — mas o
venv deste projeto (gerenciado pelo `uv`) usa 3.13, e a extensão compilada do
PyGObject é presa a uma ABI de Python exata. `--system-site-packages` não
resolve isso (o `.so` compilado pro 3.14 não carrega no 3.13). Solução: falar
o protocolo AT-SPI direto via D-Bus usando `jeepney` (pura em Python, sem
extensão compilada) — funciona independente de qual build de Python o `uv`
escolher no futuro. Protocolo confirmado empiricamente (2026-09-06) contra o
barramento de acessibilidade real: `org.a11y.Bus.GetAddress` (barramento de
sessão) dá o endereço do barramento AT-SPI dedicado; a partir daí,
`org.a11y.atspi.Accessible.{GetChildren,GetRoleName,GetInterfaces}` e
`org.a11y.atspi.Component.GetExtents` (coord_type=SCREEN=0) — mesmo espaço de
coordenadas global que `hyprctl`/`ydotool` já usam.

Cobertura real confirmada nesta máquina: GTK/Qt aparecem (`waybar`, `swaync`,
`thunar`, `xdg-desktop-portal-gtk`); Chromium/Vivaldi e Alacritty não aparecem
(sem árvore de acessibilidade nenhuma) — daí a divisão de responsabilidade com
`browser_*`/print+clique.

Detalhe de protocolo: elementos ocultos/não renderizados (ex. botões de um
menu "mais opções" fechado) retornam um sentinela `-2147483648` como
extents — filtrado antes de aparecer numa resposta (`w>0 and h>0 and x/y !=
sentinela`), senão pareceria um ponto clicável válido.

## CDP: automação de navegador sem a extensão claude-in-chrome

Requer o navegador já rodando com porta de debug aberta:

```bash
vivaldi --remote-debugging-port=9222   # ou o navegador que você usa
```

O agente pode fazer esse relaunch sozinho quando precisar (autorizado pelo
usuário em 2026-09-06 — o navegador restaura as abas ao reabrir, então fechar
pra religar com a flag não perde nada; ainda assim, perguntar antes é sempre
uma opção válida se houver dúvida sobre o momento). Sem a porta aberta,
qualquer `browser_*` levanta erro explicando esse passo.

Toda interação passa por `Runtime.evaluate` (injeção de JS pequeno) em vez do
par nativo `Input.dispatchMouseEvent`/`DOM.getBoxModel` do CDP — não precisa
da janela focada nem de matemática de coordenada de tela, e é exatamente como
um bookmarklet/extensão já interage com uma página. `browser_find` marca cada
elemento encontrado com um atributo `data-cdp-mark` único e devolve esse
`mark`; `browser_click`/`browser_type` relocalizam por esse atributo exato —
evita o bug clássico de "o seletor CSS bateu num elemento diferente da
segunda vez".

## Descobertas empíricas registradas (2026-09-05, nesta máquina)

- `ydotool mousemove --absolute -- X Y` e `hyprctl cursorpos` usam exatamente o
  mesmo espaço de coordenadas (testado: 1:1, sem necessidade de conversão de
  escala neste monitor único `scale=1`). **Não testado em multi-monitor ou
  escala != 1.**
- Códigos de botão: base (`0x00` esquerdo / `0x01` direito / `0x02` meio) OR
  `0xC0` (desce+solta) — confirmado contra `ydotool click --help` da build
  instalada (1.0.4).
- Sem subcomando de scroll dedicado nesta build (`click`, `mousemove`, `type`,
  `key`, `debug`, `bakers`) — scroll usa `mousemove --wheel`.
- Socket do `ydotoold` é `SOCK_DGRAM`, não `SOCK_STREAM` (uma tentativa de
  conexão STREAM falha com `ENOTSOCK`/"Protocol wrong type for socket").

## Riscos abertos

- Posicionamento absoluto não testado em multi-monitor/escala != 1.
- Apps sandboxed podem reagir diferente a eventos via `uinput` — testar contra
  amostra maior (navegador, apps GTK/Qt nativos) além do terminal já validado.
- `--scope user` no `claude mcp add` afeta todas as sessões futuras — decisão
  consciente, não uma formalidade.
