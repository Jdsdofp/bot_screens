"""
Captura automática de screenshots do SmartXHub.

Uso:
    python captura_screenshots.py /wo-dashboard
    python captura_screenshots.py /outro-modulo

Estrutura de saída:
    screenshots/
      <modulo>/
        01_MAINTENANCE/
          01_Dashboard_OS.png
          02_Asset_360_Health.png
          ...
        02_PREDICTIVE_MAINTENANCE/
          ...
        indice.txt
"""
import sys, io, os, re
from pathlib import Path
from playwright.sync_api import sync_playwright, Page

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Configurações ──────────────────────────────────────────────────────────────
USUARIO = "master.xusa"
SENHA = "@123456"
BASE_URL = "https://dash.smartxhub.cloud"
SESSION_FILE = "storage_state.json"
SCREENSHOTS_ROOT = Path("screenshots")
WAIT_GRAFICOS_MS = 5000


# ── Utilitários ────────────────────────────────────────────────────────────────

def slug(texto: str) -> str:
    texto = re.sub(r"[^\w\s\-]", "", texto.strip())
    texto = re.sub(r"\s+", "_", texto)
    return texto[:60]


# ── Autenticação ───────────────────────────────────────────────────────────────

def autenticar(page: Page) -> None:
    """Realiza login a partir da página de login atual."""
    page.wait_for_selector("input[type='text']", timeout=15000)
    page.locator("input[type='text']").first.fill(USUARIO)
    page.locator("input[type='password']").first.fill(SENHA)
    page.locator("button:has-text('Entrar')").first.click()
    page.wait_for_function("() => !window.location.href.includes('/login')", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    print(f"  Login realizado. URL: {page.url}")


def iniciar_sessao(browser, rota: str) -> Page:
    """Cria contexto, faz login se necessário e navega até o módulo."""
    context = browser.new_context(
        storage_state=SESSION_FILE if os.path.exists(SESSION_FILE) else None,
        viewport={"width": 1920, "height": 1080},
    )
    page = context.new_page()

    page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    if "login" in page.url.lower():
        print("Sessão expirada/inexistente. Fazendo login...")
        autenticar(page)
        context.storage_state(path=SESSION_FILE)
        print(f"  Sessão salva: {SESSION_FILE}")
    else:
        print(f"Sessão ativa: {page.url}")

    # Navega para o módulo solicitado
    _navegar_modulo(page, rota)
    return page


def _navegar_modulo(page: Page, rota: str) -> None:
    url = f"{BASE_URL}{rota}"
    if page.url == url:
        return
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    if "login" in page.url.lower():
        autenticar(page)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(2000)
    print(f"  Módulo: {page.url}")


# ── Raspagem do menu ───────────────────────────────────────────────────────────

def obter_grupos(page: Page) -> list[dict]:
    """
    Retorna lista de grupos do menu lateral.
    Estrutura DOM: aside.sidebar > nav > DIV-container > DIV-grupo[i]
    Cada grupo tem: children[0]=BUTTON (toggle), children[1..N]=<A> itens (quando expandido)
    """
    return page.evaluate("""() => {
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
        const container = nav?.children[0];
        if (!container) return [];

        // Pula children[0] ("All modules") e pega os grupos
        return Array.from(container.children).slice(1).map((grupo, i) => {
            const botao = grupo.children[0];
            const textoRaw = botao?.innerText?.trim().split('\\n')[0] || '';
            // Remove emojis/ícones do início para obter texto limpo
            const texto = textoRaw.replace(/^[^\\w]+/, '').trim();
            return {
                indice_no_container: i + 1, // +1 porque pulamos children[0]
                texto_original: textoRaw,
                texto_limpo: texto,
                filhos: grupo.children.length,
            };
        });
    }""")


def expandir_grupo(page: Page, indice_container: int) -> bool:
    """Garante que o grupo está expandido. Se já estiver aberto, não faz nada."""
    filhos = page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
        return nav?.children[0]?.children[{indice_container}]?.children.length || 0;
    }}""")
    if filhos > 1:
        return True  # já expandido, não togglea
    # Está fechado — clica no BUTTON para abrir
    page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
        const grupo = nav?.children[0]?.children[{indice_container}];
        grupo?.children[0]?.click();
    }}""")
    page.wait_for_timeout(1200)
    filhos = page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
        return nav?.children[0]?.children[{indice_container}]?.children.length || 0;
    }}""")
    return filhos > 1


def fechar_grupo(page: Page, indice_container: int) -> None:
    """Fecha o grupo clicando no BUTTON novamente."""
    filhos = page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
        return nav?.children[0]?.children[{indice_container}]?.children.length || 0;
    }}""")
    if filhos > 1:
        page.evaluate(f"""() => {{
            const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
            const grupo = nav?.children[0]?.children[{indice_container}];
            grupo?.children[0]?.click();
        }}""")
        page.wait_for_timeout(600)


def obter_itens_grupo(page: Page, indice_container: int) -> list[dict]:
    """
    Coleta os itens (<A>) do grupo expandido.
    Itens são children[1..N] do grupo (após o BUTTON que é children[0]).
    """
    return page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
        const grupo = nav?.children[0]?.children[{indice_container}];
        if (!grupo || grupo.children.length <= 1) return [];

        // Pula children[0] (BUTTON) e coleta todos os <A>
        return Array.from(grupo.children).slice(1).map((el, i) => {{
            const rect = el.getBoundingClientRect();
            return {{
                indice: i,
                tag: el.tagName,
                texto: el.innerText?.trim().split('\\n')[0]?.trim() || '',
                href: el.getAttribute('href') || el.href || '',
                y: Math.round(rect.top),
            }};
        }}).filter(it => it.texto.length > 0);
    }}""")


# ── Navegação e captura ────────────────────────────────────────────────────────

def clicar_item(page: Page, indice_container: int, indice_item: int) -> bool:
    """Clica no item via SPA (mantém sidebar expandido e item ativo destacado)."""
    return bool(page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar');
        const grupo = nav?.children[0]?.children[{indice_container}];
        const item = grupo?.children[{indice_item + 1}];
        if (!item) return false;
        item.click();
        return true;
    }}"""))


def capturar_pagina(page: Page, caminho: Path) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(WAIT_GRAFICOS_MS)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(caminho), full_page=False)
    print(f"    Salvo: {caminho}")


# ── Principal ──────────────────────────────────────────────────────────────────

def main(rota: str) -> None:
    pasta_modulo = SCREENSHOTS_ROOT / slug(rota.strip("/"))

    print(f"\n{'='*60}")
    print(f"Módulo : {rota}")
    print(f"Saída  : {pasta_modulo.resolve()}")
    print(f"{'='*60}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = iniciar_sessao(browser, rota)

        grupos = obter_grupos(page)
        if not grupos:
            print("Nenhum grupo de menu encontrado. Verifique a rota.")
            browser.close()
            return

        print(f"\nGrupos encontrados ({len(grupos)}):")
        for g in grupos:
            print(f"  [{g['indice_no_container']}] {g['texto_limpo']}")

        # Processa cada grupo
        for num_grupo, grupo in enumerate(grupos, start=1):
            nome_grupo = grupo['texto_limpo']
            idx = grupo['indice_no_container']
            pasta_grupo = pasta_modulo / f"{num_grupo:02d}_{slug(nome_grupo)}"

            print(f"\n[{num_grupo}/{len(grupos)}] Grupo: {nome_grupo}")

            # Garante que estamos no módulo antes de expandir
            _navegar_modulo(page, rota)
            page.wait_for_timeout(500)

            # Expande o grupo
            expandiu = expandir_grupo(page, idx)
            if not expandiu:
                print(f"  Não expandiu (sem itens ou grupo vazio).")
                continue

            # Coleta itens
            itens = obter_itens_grupo(page, idx)
            if not itens:
                print(f"  Nenhum item encontrado após expandir.")
                fechar_grupo(page, idx)
                continue

            print(f"  {len(itens)} itens:")
            for it in itens:
                print(f"    • {it['texto']}")

            # Captura cada item mantendo o menu expandido (SPA navigation)
            for it in itens:
                nome_item = it['texto']
                caminho = pasta_grupo / f"{it['indice']+1:02d}_{slug(nome_item)}.png"
                print(f"  [{it['indice']+1}/{len(itens)}] {nome_item}")

                # Re-expande o grupo se necessário (pode ter fechado em alguma navegação)
                expandir_grupo(page, idx)
                page.wait_for_timeout(300)

                if clicar_item(page, idx, it['indice']):
                    capturar_pagina(page, caminho)
                else:
                    print(f"    Pulado (item não encontrado).")

            # Volta ao módulo antes do próximo grupo
            _navegar_modulo(page, rota)

        browser.close()

    print(f"\n{'='*60}")
    print(f"Concluído! Screenshots em: {pasta_modulo.resolve()}")
    print(f"{'='*60}\n")
    _salvar_indice(pasta_modulo)


def _salvar_indice(pasta: Path) -> None:
    linhas = [f"# Índice de capturas — {pasta.name}\n"]
    for grupo_dir in sorted(pasta.iterdir()):
        if not grupo_dir.is_dir():
            continue
        linhas.append(f"\n## {grupo_dir.name}")
        for png in sorted(grupo_dir.glob("*.png")):
            linhas.append(f"  - {png.name}")
    indice = pasta / "indice.txt"
    indice.write_text("\n".join(linhas), encoding="utf-8")
    print(f"Índice salvo: {indice}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python captura_screenshots.py /rota-do-modulo")
        print("Ex:  python captura_screenshots.py /wo-dashboard")
        sys.exit(1)
    rota = sys.argv[1]
    if not rota.startswith("/"):
        rota = "/" + rota
    main(rota)
