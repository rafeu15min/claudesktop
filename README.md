# claudesktop

Servidor MCP local (Python) que expõe controle de mouse/teclado com consciência de
geometria de janela para o Claude Code, no lugar da gambiarra de comandos `bash`
soltos com coordenadas chutadas a partir de um print.

Ferramentas: listar janelas, screenshot (tela cheia/monitor/região), mover mouse
(absoluto ou até uma janela por âncora), clicar, scroll, digitar texto, teclas de
atalho, e um diagnóstico só-leitura (`check_environment`). Toda ferramenta que
muda estado devolve um screenshot novo junto da resposta — é assim que o agente
(sem percepção contínua da tela) confirma o efeito de cada ação.

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

Fora do escopo do v1 (não construído especulativamente): drag-and-drop,
multi-touch, clipboard, mover/fechar janelas, OCR, captura contínua/vídeo, e a
captura de debugging de teclado físico descrita no plano original (recurso
maior e sensível — ver plano antes de implementar).

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
