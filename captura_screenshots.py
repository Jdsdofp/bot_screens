"""
Captura automática de screenshots do SmartXHub com exploração de componentes.

Uso:
    python captura_screenshots.py /wo-dashboard
    python captura_screenshots.py /outro-modulo

Estrutura de saída:
    screenshots/
      <modulo>/
        01_MAINTENANCE/
          01_Dashboard_OS/
            00_inicial.png
            01_Tab_Overview.png
            02_Tab_Details.png
          02_Asset_360_Health/
            00_inicial.png
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
SESSION_FILE = str(Path(__file__).parent / "storage_state.json")
SCREENSHOTS_ROOT = Path(__file__).parent / "screenshots"
WAIT_GRAFICOS_MS = 5000   # Aguarda carregamento de gráficos/dados
WAIT_INTERACAO_MS = 1500  # Aguarda após clicar em componente


# ── Utilitários ────────────────────────────────────────────────────────────────

def slug(texto: str) -> str:
    texto = re.sub(r"[^\w\s\-]", "", texto.strip())
    texto = re.sub(r"\s+", "_", texto)
    return texto[:60]


# ── Autenticação ───────────────────────────────────────────────────────────────

def autenticar(page: Page) -> None:
    page.wait_for_selector("input[type='text'], input[type='email']", timeout=20000)
    page.locator("input[type='text'], input[type='email']").first.fill(USUARIO)
    page.locator("input[type='password']").first.fill(SENHA)
    # Tenta variações de texto do botão de submit
    btn = page.locator(
        "button:has-text('Entrar'), button:has-text('Login'), "
        "button:has-text('Sign in'), button[type='submit']"
    ).first
    btn.click(timeout=20000)
    page.wait_for_function("() => !window.location.href.includes('/login')", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    print(f"  Login realizado. URL: {page.url}")


def iniciar_sessao(browser, rota: str) -> Page:
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

    _navegar_modulo(page, rota)
    return page


def _pagina_com_erro(page: Page) -> bool:
    """Retorna True se a página atual é uma página de erro (403, nginx, etc.)."""
    titulo = page.title().lower()
    if any(c in titulo for c in ("403", "404", "forbidden", "not found", "error")):
        return True
    corpo = page.evaluate("() => document.body?.innerText?.slice(0, 200) || ''")
    return "403 Forbidden" in corpo or "404 Not Found" in corpo


def _navegar_via_link(page: Page, rota: str) -> bool:
    """Fallback SPA: vai ao /home e clica no elemento que leva à rota."""
    import re as _re
    home_url = f"{BASE_URL}/home"
    if page.url != home_url:
        page.goto(home_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)

    seg = rota.strip("/").split("/")[0]

    def _ok():
        return not _pagina_com_erro(page) and page.url != home_url

    # 1) <a href>
    try:
        loc = page.locator(f'a[href="{rota}"], a[href^="{rota}/"]').first
        if loc.count() > 0:
            loc.click(timeout=4000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(1000)
            if _ok(): return True
    except Exception:
        pass

    # 2) router-link / [href*=rota]
    try:
        loc = page.locator(f'[href*="{rota}"], [to="{rota}"]').first
        if loc.count() > 0:
            loc.click(timeout=4000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(1000)
            if _ok(): return True
    except Exception:
        pass

    # 3) Texto com slug
    try:
        loc = page.get_by_text(_re.compile(seg, _re.IGNORECASE)).first
        if loc.count() > 0:
            loc.click(timeout=4000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(1000)
            if _ok(): return True
    except Exception:
        pass

    # 4) JS: busca em todos os elementos
    clicou = page.evaluate(f"""() => {{
        const seg = {repr(seg)};
        const alvo = {repr(rota)};
        const base = window.location.origin;
        const els = Array.from(document.body.querySelectorAll('*')).reverse();
        for (const el of els) {{
            const href = el.getAttribute('href') || el.href || '';
            if (href && (href === alvo || href === base + alvo || href.includes(alvo))) {{
                el.click(); return 'href:' + href;
            }}
            const txt = (el.innerText || '').toLowerCase().trim();
            if (txt.includes(seg) && txt.length < 120 && el.children.length <= 5) {{
                const rect = el.getBoundingClientRect();
                if (rect.width > 30 && rect.height > 10) {{
                    el.click(); return 'txt:' + txt.slice(0, 40);
                }}
            }}
        }}
        const hrefs = [...new Set(
            Array.from(document.querySelectorAll('[href]'))
                .map(e => e.getAttribute('href')).filter(Boolean)
        )].slice(0, 25);
        return 'none:' + JSON.stringify(hrefs);
    }}""")

    if clicou and not clicou.startswith('none:'):
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        if _ok(): return True
    elif clicou and clicou.startswith('none:'):
        print(f"  Links disponíveis no /home: {clicou[5:]}")

    # 5) history.pushState
    try:
        page.evaluate(f"""() => {{
            window.history.pushState({{path: {repr(rota)}}}, '', {repr(rota)});
            window.dispatchEvent(new PopStateEvent('popstate', {{state: {{path: {repr(rota)}}}}}));
        }}""")
        page.wait_for_timeout(3000)
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(1000)
    except Exception:
        pass

    return not _pagina_com_erro(page) and rota in page.url


def _navegar_modulo(page: Page, rota: str, titulo_esperado: str = "") -> bool:
    """Navega para a rota e valida pelo título do módulo se fornecido.
    Retorna False se o título carregado não bater com o esperado."""
    url = f"{BASE_URL}{rota}"
    if page.url == url:
        page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    if "login" in page.url.lower():
        autenticar(page)
    # Se recebeu 403/erro: tenta navegar via clique no link (rota SPA bloqueada para acesso direto)
    if _pagina_com_erro(page):
        print(f"  Rota '{rota}' bloqueada — navegando via link SPA...")
        ok = _navegar_via_link(page, rota)
        if not ok:
            print(f"  ERRO: não foi possível navegar para '{rota}' nem via link.")
            return False
        print(f"  Navegação via link OK → {page.url}")
    else:
        page.wait_for_load_state("networkidle", timeout=20000)
        page.wait_for_timeout(2000)
    print(f"  Módulo: {page.url}")
    if titulo_esperado:
        titulo_atual = obter_app_atual(page)
        if titulo_atual and titulo_atual.lower() != titulo_esperado.lower():
            print(f"  AVISO: título do módulo mudou! Esperado='{titulo_esperado}' Atual='{titulo_atual}'")
            return False
    return True


# ── Raspagem do menu ───────────────────────────────────────────────────────────

def obter_app_atual(page: Page) -> str:
    """Lê o título do módulo atual. Tenta breadcrumb da página e depois o sidebar."""
    return page.evaluate("""() => {
        // 1) Breadcrumb no topo do conteúdo (ex: "Stock Control · Parts Dashboard" → "Stock Control")
        const breadcrumbSels = [
            'nav[aria-label="breadcrumb"] li:first-child',
            '[class*="breadcrumb"] :first-child',
            'header [class*="breadcrumb"]',
            // SmartXHub: elemento de texto antes do "·" no header do conteúdo
            'main header span:first-child',
            '[class*="pageHeader"] span:first-child',
            '[class*="page-header"] span:first-child',
        ];
        for (const sel of breadcrumbSels) {
            const el = document.querySelector(sel);
            const t = el?.innerText?.trim().split(/[·>\/\\n]/)[0]?.trim();
            if (t && t.length > 2 && t.length < 60) return t;
        }

        // 2) Título no topo do sidebar (children[1] = nome do módulo; children[0] = "← All modules")
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        const container = nav?.children[0];
        if (container && container.children.length > 1) {
            const t = container.children[1]?.innerText?.trim().split('\\n')[0]?.trim();
            if (t && t.length > 2 && !t.includes('←') && !t.includes('All modules')) return t;
        }
        // Fallback: children[0] (caso não haja botão "← All modules")
        return container?.children[0]?.innerText?.trim().split('\\n')[0]?.trim() || '';
    }""")


def obter_grupos(page: Page) -> list[dict]:
    return page.evaluate("""() => {
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        const container = nav?.children[0];
        if (!container) return [];
        return Array.from(container.children).slice(1).map((grupo, i) => {
            const botao = grupo.children[0];
            const textoRaw = botao?.innerText?.trim().split('\\n')[0] || '';
            const texto = textoRaw.replace(/^[^\\w]+/, '').trim();
            return {
                indice_no_container: i + 1,
                texto_original: textoRaw,
                texto_limpo: texto,
                filhos: grupo.children.length,
            };
        });
    }""")


def expandir_grupo(page: Page, indice_container: int) -> bool:
    filhos = page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        return nav?.children[0]?.children[{indice_container}]?.children.length || 0;
    }}""")
    if filhos > 1:
        return True
    page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        const grupo = nav?.children[0]?.children[{indice_container}];
        grupo?.children[0]?.click();
    }}""")
    page.wait_for_timeout(1200)
    filhos = page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        return nav?.children[0]?.children[{indice_container}]?.children.length || 0;
    }}""")
    return filhos > 1


def fechar_grupo(page: Page, indice_container: int) -> None:
    filhos = page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        return nav?.children[0]?.children[{indice_container}]?.children.length || 0;
    }}""")
    if filhos > 1:
        page.evaluate(f"""() => {{
            const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
            const grupo = nav?.children[0]?.children[{indice_container}];
            grupo?.children[0]?.click();
        }}""")
        page.wait_for_timeout(600)


def obter_itens_grupo(page: Page, indice_container: int) -> list[dict]:
    return page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        const grupo = nav?.children[0]?.children[{indice_container}];
        if (!grupo || grupo.children.length <= 1) return [];
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


# ── Navegação ──────────────────────────────────────────────────────────────────

def clicar_item(page: Page, indice_container: int, indice_item: int) -> bool:
    return bool(page.evaluate(f"""() => {{
        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
        const grupo = nav?.children[0]?.children[{indice_container}];
        const item = grupo?.children[{indice_item + 1}];
        if (!item) return false;
        item.click();
        return true;
    }}"""))


# ── Exploração de componentes ──────────────────────────────────────────────────

def explorar_pagina(page: Page, pasta: Path, log=print, re_expandir=None) -> None:
    """
    Captura estado inicial + componentes interativos da página:
      - Tabs (role="tab" ou grupo horizontal de botões tipo aba)
      - Filtros de período (Today, 7d, 30d, etc.)
      - Seções expansíveis tipo accordion (aria-expanded=false em headers)
    """
    pasta = pasta.resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    contador = [0]
    url_inicial = page.url

    def snap(nome: str) -> None:
        # Reseta o scroll antes de cada print (cliques podem rolar a página e cortar o menu)
        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        # Re-expande o sidebar antes de cada print (o SPA pode recolhê-lo ao interagir)
        if re_expandir:
            try:
                re_expandir()
                page.wait_for_timeout(200)
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(WAIT_INTERACAO_MS)
        path = pasta / f"{contador[0]:02d}_{slug(nome)}.png"
        page.screenshot(path=str(path), full_page=False)
        log(f"      {path.name}")
        contador[0] += 1

    def ainda_na_pagina() -> bool:
        return page.url == url_inicial

    def recuperar_pagina() -> None:
        if not ainda_na_pagina():
            page.goto(url_inicial, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

    # ── 00: Estado inicial ─────────────────────────────────────────────────────
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(WAIT_GRAFICOS_MS)
    snap("inicial")

    # ── Tabs ───────────────────────────────────────────────────────────────────
    tabs = page.evaluate("""() => {
        const CONTEUDO_MIN_X = 200;
        const CONTEUDO_MIN_Y = 80; // exclui cabeçalho fixo (logo, SmartX AI, notificações, etc.)
        // Botões utilitários globais do SmartXHub — não são tabs de conteúdo
        const UTIL = /^(limpar|clear|chat|report|smartx|smart\s*x|ajuda|help|suporte|support|fechar|close|export|exportar|csv|excel|download|refresh|atualizar|imprimir|print|salvar|save|cancelar|cancel|confirmar|confirm|novo|new|criar|create|adicionar|add)$/i;
        const UTIL_CONTEM = /smart\s*x|ai insights|my applications|hub insight/i;
        // Remove ícones/emojis/símbolos do início do texto antes de testar os filtros,
        // pois botões utilitários (Chat, Report, Refresh...) vêm com um ícone colado à frente
        // e isso quebrava o match ancorado (^...$) do UTIL, deixando o filtro passar batido
        const limpar = (t) => t.replace(/^[^a-zA-Z0-9À-ÿ]+/, '').trim();
        const PERIGO = /delete|exclu|remov|logout|sair/i;
        // Inclui formatos de período estilo "1y", "6m", "1w" além dos já existentes
        const RE_PERIODO = /^(7|14|30|60|90|180|365)d?$|^(1|3|6|12)m$|^[1-9][0-9]*[yY]$|^[1-9]w$|^(today|yesterday|week|month|year|semana|mes|ano)$/i;
        const vistos = new Set();
        const resultado = [];

        // Método 1: ARIA tabs (role="tab" apenas — não pega filhos genéricos de tablist)
        document.querySelectorAll('[role="tab"]').forEach(el => {
            const rect = el.getBoundingClientRect();
            const textoRaw = el.innerText?.trim().split('\\n')[0]?.trim() || '';
            const texto = limpar(textoRaw);
            if (!texto || texto.length < 2 || texto.length > 60) return;
            // Exige pelo menos uma letra — filtra badges numéricos como "8634"
            if (!/[a-zA-Z]/.test(texto)) return;
            if (rect.width === 0 || rect.height === 0 || rect.left <= CONTEUDO_MIN_X || rect.top <= CONTEUDO_MIN_Y) return;
            if (PERIGO.test(texto) || UTIL.test(texto) || UTIL_CONTEM.test(texto) || RE_PERIODO.test(texto)) return;
            if (vistos.has(texto)) return;
            vistos.add(texto);
            resultado.push({ texto, x: Math.round(rect.left + rect.width/2), y: Math.round(rect.top + rect.height/2) });
        });

        // Método 2: grupo horizontal de botões tipo aba (só se não achou ARIA tabs)
        if (resultado.length === 0) {
            document.querySelectorAll('button, [role="button"]').forEach(btn => {
                const pai = btn.parentElement;
                if (!pai) return;
                const irmaos = Array.from(pai.children).filter(c =>
                    c.tagName === 'BUTTON' || c.getAttribute('role') === 'button'
                );
                if (irmaos.length < 2 || irmaos.length > 8) return;
                const rect = btn.getBoundingClientRect();
                if (rect.left <= CONTEUDO_MIN_X || rect.top <= CONTEUDO_MIN_Y || rect.width === 0) return;
                const ys = irmaos.map(b => b.getBoundingClientRect().top);
                if (Math.max(...ys) - Math.min(...ys) >= 10) return;
                const textoRaw = btn.innerText?.trim().split('\\n')[0]?.trim() || '';
                const texto = limpar(textoRaw);
                if (!texto || texto.length < 2 || texto.length > 40) return;
                if (!/[a-zA-Z]/.test(texto)) return;
                if (PERIGO.test(texto) || UTIL.test(texto) || UTIL_CONTEM.test(texto) || RE_PERIODO.test(texto) || vistos.has(texto)) return;
                vistos.add(texto);
                resultado.push({ texto, x: Math.round(rect.left + rect.width/2), y: Math.round(rect.top + rect.height/2) });
            });
        }

        return resultado;
    }""")

    if len(tabs) > 1:
        log(f"    [{len(tabs)} tabs encontradas]")
        for tab in tabs:
            page.mouse.click(tab["x"], tab["y"])
            page.wait_for_timeout(600)
            if ainda_na_pagina():
                snap(f"Tab_{tab['texto']}")
            else:
                recuperar_pagina()

    # ── Filtros de período ─────────────────────────────────────────────────────
    filtros = page.evaluate("""() => {
        const RE = /^(7|14|30|60|90|180|365)d?$|^(1|3|6|12)m$|^[12]?[yw]$|^(today|yesterday|week|month|year|semana|mes|ano|quarter|trimestre|dia)$/i;
        const vistos = new Set();
        return Array.from(document.querySelectorAll('button, [role="button"]')).map(el => {
            const rect = el.getBoundingClientRect();
            const texto = el.innerText?.trim().replace(/\\s+/g, ' ') || '';
            return {
                texto,
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
                ok: rect.width > 0 && rect.height > 0 && rect.left > 200 && RE.test(texto),
            };
        }).filter(f => {
            if (!f.ok || vistos.has(f.texto)) return false;
            vistos.add(f.texto);
            return true;
        });
    }""")

    if filtros:
        log(f"    [{len(filtros)} filtros de período]")
        for filtro in filtros:
            page.mouse.click(filtro["x"], filtro["y"])
            page.wait_for_timeout(500)
            if ainda_na_pagina():
                snap(f"Filtro_{filtro['texto']}")
            else:
                recuperar_pagina()

    # ── Seções expansíveis (accordion) ────────────────────────────────────────
    # Só considera elementos que parecem cabeçalhos de seção:
    # têm aria-expanded=false, estão fora do sidebar, e têm texto de título
    expandaveis = page.evaluate("""() => {
        const vistos = new Set();
        return Array.from(document.querySelectorAll(
            '[aria-expanded="false"][role="button"], ' +
            '[aria-expanded="false"][role="tab"], ' +
            'button[aria-expanded="false"], ' +
            'h1[aria-expanded="false"], h2[aria-expanded="false"], ' +
            'h3[aria-expanded="false"], h4[aria-expanded="false"], ' +
            '[aria-expanded="false"].accordion, ' +
            '[aria-expanded="false"][class*="accordion"], ' +
            '[aria-expanded="false"][class*="collapse"]'
        )).map(el => {
            const rect = el.getBoundingClientRect();
            const texto = (el.innerText?.trim().split('\\n')[0]?.trim()
                        || el.getAttribute('aria-label')
                        || el.getAttribute('title') || '').slice(0, 60);
            return {
                texto,
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
                ok: rect.width > 0 && rect.height > 0 && rect.left > 200 && texto.length > 1,
            };
        }).filter(e => {
            if (!e.ok || vistos.has(e.texto)) return false;
            vistos.add(e.texto);
            return true;
        });
    }""")

    if expandaveis:
        log(f"    [{len(expandaveis)} seções expansíveis]")
        for exp in expandaveis[:8]:
            page.mouse.click(exp["x"], exp["y"])
            page.wait_for_timeout(600)
            if not ainda_na_pagina():
                recuperar_pagina()
                continue
            snap(f"Expandido_{exp['texto']}")
            # Fecha de volta
            page.mouse.click(exp["x"], exp["y"])
            page.wait_for_timeout(300)


# ── Principal ──────────────────────────────────────────────────────────────────

def main(rota: str) -> None:
    pasta_modulo = SCREENSHOTS_ROOT / slug(rota.strip("/"))

    print(f"\n{'='*60}")
    print(f"Módulo : {rota}")
    print(f"Saída  : {pasta_modulo.resolve()}")
    print(f"{'='*60}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = iniciar_sessao(browser, rota)
        app_esperado = obter_app_atual(page)
        if app_esperado:
            print(f"Título do módulo: '{app_esperado}'")
        else:
            print("AVISO: título do módulo não detectado — a validação por título será ignorada.")

        grupos = obter_grupos(page)
        if not grupos:
            print("Nenhum grupo de menu encontrado — capturando página diretamente.")
            pasta_modulo.mkdir(parents=True, exist_ok=True)
            explorar_pagina(page, pasta_modulo, log=print)
            browser.close()
            print(f"\n{'='*60}")
            print(f"Concluído! Screenshots em: {pasta_modulo.resolve()}")
            print(f"{'='*60}\n")
            _salvar_indice(pasta_modulo)
            return

        print(f"\nGrupos encontrados ({len(grupos)}):")
        for g in grupos:
            print(f"  [{g['indice_no_container']}] {g['texto_limpo']}")

        for num_grupo, grupo in enumerate(grupos, start=1):
            nome_grupo = grupo["texto_limpo"]
            idx = grupo["indice_no_container"]
            pasta_grupo = pasta_modulo / f"{num_grupo:02d}_{slug(nome_grupo)}"

            print(f"\n[{num_grupo}/{len(grupos)}] Grupo: {nome_grupo}")

            ok = _navegar_modulo(page, rota, titulo_esperado=app_esperado)
            if not ok:
                print(f"  Pulado (sidebar de outro módulo carregou após re-navegação).")
                continue
            page.wait_for_timeout(500)

            expandiu = expandir_grupo(page, idx)
            if not expandiu:
                print(f"  Não expandiu (sem itens ou grupo vazio).")
                continue

            itens = obter_itens_grupo(page, idx)
            if not itens:
                print(f"  Nenhum item encontrado após expandir.")
                fechar_grupo(page, idx)
                continue

            print(f"  {len(itens)} itens:")
            for it in itens:
                print(f"    • {it['texto']}")

            for it in itens:
                nome_item = it["texto"]
                pasta_pagina = pasta_grupo / f"{it['indice']+1:02d}_{slug(nome_item)}"
                print(f"  [{it['indice']+1}/{len(itens)}] {nome_item}")

                href = it.get("href", "")
                if href:
                    # Navega direto pela URL — evita o bug de o SPA remover itens visitados do DOM
                    dest = href if href.startswith("http") else f"{BASE_URL}{href}"
                    try:
                        page.goto(dest, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(1000)
                        if "login" in page.url.lower():
                            autenticar(page)
                        # Se o link levou para outro módulo da suíte, pula (valida pelo título)
                        app_destino = obter_app_atual(page)
                        if app_esperado and app_destino and app_destino.lower() != app_esperado.lower():
                            print(f"    Pulado (módulo diferente: '{app_destino}' ≠ '{app_esperado}')")
                            continue
                        # Re-expande o grupo para o sidebar aparecer aberto no print
                        expandir_grupo(page, idx)
                        page.wait_for_timeout(300)
                        explorar_pagina(page, pasta_pagina, log=print,
                                        re_expandir=lambda i=idx: expandir_grupo(page, i))
                    except Exception as e:
                        print(f"    Erro ao navegar: {e}")
                else:
                    # Fallback: SPA click (para itens sem href)
                    expandir_grupo(page, idx)
                    page.wait_for_timeout(300)
                    if clicar_item(page, idx, it["indice"]):
                        explorar_pagina(page, pasta_pagina, log=print,
                                        re_expandir=lambda i=idx: expandir_grupo(page, i))
                    else:
                        print(f"    Pulado (item sem href e sem elemento DOM).")

            _navegar_modulo(page, rota, titulo_esperado=app_esperado)

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
        for item in sorted(grupo_dir.iterdir()):
            if item.is_dir():
                pngs = sorted(item.glob("*.png"))
                linhas.append(f"\n  ### {item.name}  ({len(pngs)} prints)")
                for png in pngs:
                    linhas.append(f"    - {png.name}")
            elif item.suffix == ".png":
                linhas.append(f"  - {item.name}")
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
