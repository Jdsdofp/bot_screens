"""
SmartXHub Screenshot Bot — Interface Qt
"""
import sys
import os
import re
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QFrame, QMessageBox,
    QProgressBar, QCheckBox, QScrollArea, QSizePolicy, QFileDialog, QComboBox,
    QDialog, QGridLayout,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QSize
from PyQt6.QtGui import QFont, QPixmap

# ── Configurações ──────────────────────────────────────────────────────────────
BASE_URL = "https://dash.smartxhub.cloud"
_BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
SESSION_FILE = str(_BASE_DIR / "storage_state.json")
SCREENSHOTS_ROOT = _BASE_DIR / "screenshots"
WAIT_GRAFICOS_MS = 5000
CONFIG_FILE = str(_BASE_DIR / "config.txt")


# ── Utilitários ────────────────────────────────────────────────────────────────

def slug(texto: str) -> str:
    texto = re.sub(r"[^\w\s\-]", "", texto.strip())
    texto = re.sub(r"\s+", "_", texto)
    return texto[:60]


def salvar_config(usuario: str, rota: str, base_url: str = "",
                  pasta_saida: str = "", idioma: str = ""):
    try:
        Path(CONFIG_FILE).write_text(
            f"{usuario}\n{rota}\n{base_url}\n{pasta_saida}\n{idioma}", encoding="utf-8"
        )
    except Exception:
        pass


def carregar_config() -> tuple:
    try:
        linhas = Path(CONFIG_FILE).read_text(encoding="utf-8").splitlines()
        u = linhas[0] if len(linhas) > 0 else ""
        r = linhas[1] if len(linhas) > 1 else "/wo-dashboard"
        b = linhas[2] if len(linhas) > 2 else BASE_URL
        p = linhas[3] if len(linhas) > 3 else ""
        i = linhas[4] if len(linhas) > 4 else ""
        return u, r, b, p, i
    except Exception:
        return "", "/wo-dashboard", BASE_URL, "", ""


# ── Worker (captura em background) ────────────────────────────────────────────

class CapturaWorker(QObject):
    log = pyqtSignal(str)
    concluido = pyqtSignal(bool, str)
    progresso = pyqtSignal(int, int)
    screenshot_capturado = pyqtSignal(str)

    def __init__(self, usuario: str, senha: str, rotas: list,
                 gravar_video: bool = False, base_url: str = "",
                 pasta_saida: str = "", idioma: str = ""):
        super().__init__()
        self.usuario = usuario
        self.senha = senha
        self.rotas = rotas  # lista de rotas a processar
        self.gravar_video = gravar_video
        self.base_url = base_url.rstrip("/") or BASE_URL
        self.pasta_saida = Path(pasta_saida) if pasta_saida else SCREENSHOTS_ROOT
        self.idioma = idioma

    def run(self):
        _browsers_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "SmartXHubBot", "browsers"
        )
        os.makedirs(_browsers_dir, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _browsers_dir

        if not self._verificar_playwright():
            self.concluido.emit(False, "")
            return

        from playwright.sync_api import sync_playwright

        usuario, senha = self.usuario, self.senha
        _base_url = self.base_url

        _EXTRA_WAIT = 500 if self.gravar_video else 0

        def autenticar(page):
            page.wait_for_selector("input[type='text'], input[type='email']", timeout=20000)
            page.locator("input[type='text'], input[type='email']").first.fill(usuario)
            page.locator("input[type='password']").first.fill(senha)
            btn = page.locator(
                "button:has-text('Entrar'), button:has-text('Login'), "
                "button:has-text('Sign in'), button[type='submit']"
            ).first
            btn.click(timeout=20000)
            page.wait_for_function("() => !window.location.href.includes('/login')", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            self.log.emit(f"  Login OK → {page.url}")

        def _pagina_com_erro(page):
            titulo = page.title().lower()
            if any(c in titulo for c in ("403", "404", "forbidden", "not found", "error")):
                return True
            corpo = page.evaluate("() => document.body?.innerText?.slice(0, 200) || ''")
            return "403 Forbidden" in corpo or "404 Not Found" in corpo

        def _navegar_via_link(page, rota) -> bool:
            home_url = f"{_base_url}/home"
            if page.url != home_url:
                page.goto(home_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)

            seg = rota.strip("/").split("/")[0]

            def _ok():
                return not _pagina_com_erro(page) and page.url != home_url

            try:
                loc = page.locator(f'a[href="{rota}"], a[href^="{rota}/"]').first
                if loc.count() > 0:
                    loc.click(timeout=4000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                    page.wait_for_timeout(1000)
                    if _ok():
                        return True
            except Exception:
                pass

            try:
                loc = page.locator(f'[href*="{rota}"], [to="{rota}"]').first
                if loc.count() > 0:
                    loc.click(timeout=4000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                    page.wait_for_timeout(1000)
                    if _ok():
                        return True
            except Exception:
                pass

            try:
                import re as _re
                loc = page.get_by_text(_re.compile(seg, _re.IGNORECASE)).first
                if loc.count() > 0:
                    loc.click(timeout=4000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                    page.wait_for_timeout(1000)
                    if _ok():
                        return True
            except Exception:
                pass

            clicou = page.evaluate(f"""() => {{
                const seg = {repr(seg)};
                const alvo = {repr(rota)};
                const els = Array.from(document.body.querySelectorAll('*')).reverse();
                for (const el of els) {{
                    const href = el.getAttribute('href') || el.href || '';
                    if (href && (href.endsWith(alvo) || href.includes(alvo))) {{
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
                if _ok():
                    return True
            elif clicou and clicou.startswith('none:'):
                self.log.emit(f"  Links disponíveis no /home: {clicou[5:]}")

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

        def navegar_modulo(page, rota):
            url = f"{_base_url}{rota}"
            if page.url == url:
                page.goto(f"{_base_url}/home", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(800)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            if "login" in page.url.lower():
                autenticar(page)
            if _pagina_com_erro(page):
                self.log.emit(f"  Rota bloqueada — navegando via link SPA...")
                ok = _navegar_via_link(page, rota)
                if not ok:
                    self.log.emit(f"  ERRO: não foi possível navegar para '{rota}'.")
                    return False
                self.log.emit(f"  Navegado via SPA → {page.url}")
            else:
                page.wait_for_load_state("networkidle", timeout=20000)
                page.wait_for_timeout(2000)
            return True

        def expandir_grupo(page, idx):
            _NAV = """document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]')"""
            filhos = page.evaluate(f"""() => {{
                const nav = {_NAV};
                return nav?.children[0]?.children[{idx}]?.children.length || 0;
            }}""")
            if filhos > 1:
                return True
            page.evaluate(f"""() => {{
                const nav = {_NAV};
                const grupo = nav?.children[0]?.children[{idx}];
                grupo?.children[0]?.click();
            }}""")
            page.wait_for_timeout(1200)
            filhos = page.evaluate(f"""() => {{
                const nav = {_NAV};
                return nav?.children[0]?.children[{idx}]?.children.length || 0;
            }}""")
            return filhos > 1

        def fechar_grupo(page, idx):
            _NAV = """document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]')"""
            filhos = page.evaluate(f"""() => {{
                const nav = {_NAV};
                return nav?.children[0]?.children[{idx}]?.children.length || 0;
            }}""")
            if filhos > 1:
                page.evaluate(f"""() => {{
                    const nav = {_NAV};
                    const grupo = nav?.children[0]?.children[{idx}];
                    grupo?.children[0]?.click();
                }}""")
                page.wait_for_timeout(600)

        def obter_itens(page, idx):
            _NAV = """document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]')"""
            return page.evaluate(f"""() => {{
                const nav = {_NAV};
                const grupo = nav?.children[0]?.children[{idx}];
                if (!grupo || grupo.children.length <= 1) return [];
                return Array.from(grupo.children).slice(1).map((el, i) => ({{
                    indice: i,
                    texto: el.innerText?.trim().split('\\n')[0]?.trim() || '',
                    href: el.getAttribute('href') || el.href || '',
                }})).filter(it => it.texto.length > 0);
            }}""")

        def clicar_item(page, idx, indice_item):
            _NAV = """document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]')"""
            return bool(page.evaluate(f"""() => {{
                const nav = {_NAV};
                const grupo = nav?.children[0]?.children[{idx}];
                const item = grupo?.children[{indice_item + 1}];
                if (!item) return false;
                item.click();
                return true;
            }}"""))

        WAIT_INTERACAO_MS = 800 if self.gravar_video else 1500

        def _aguardar_render(page, timeout_extra=0):
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_function(
                    """() => {
                        const els = document.querySelectorAll('*');
                        for (const el of els) {
                            const s = window.getComputedStyle(el);
                            if (s.animationName !== 'none' && s.animationPlayState === 'running')
                                return false;
                        }
                        return true;
                    }""",
                    timeout=3000,
                )
            except Exception:
                pass
            if timeout_extra > 0:
                page.wait_for_timeout(timeout_extra)

        def explorar_pagina(page, pasta, re_expandir=None):
            pasta = pasta.resolve()
            pasta.mkdir(parents=True, exist_ok=True)
            contador = [0]
            url_inicial = page.url

            def snap(nome):
                try:
                    page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass
                if re_expandir:
                    try:
                        re_expandir()
                        page.wait_for_timeout(200)
                    except Exception:
                        pass
                _aguardar_render(page, timeout_extra=_EXTRA_WAIT)
                page.wait_for_timeout(WAIT_INTERACAO_MS)
                path = pasta / f"{contador[0]:02d}_{slug(nome)}.png"
                page.screenshot(path=str(path), full_page=False)
                self.log.emit(f"      {path.name}")
                self.screenshot_capturado.emit(str(path))
                contador[0] += 1

            def ainda_na_pagina():
                return page.url == url_inicial

            def recuperar_pagina():
                if not ainda_na_pagina():
                    page.goto(url_inicial, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)

            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(3000 if self.gravar_video else WAIT_GRAFICOS_MS)
            snap("inicial")

            tabs = page.evaluate("""() => {
                const CONTEUDO_MIN_X = 200;
                const CONTEUDO_MIN_Y = 80;
                const UTIL = /^(limpsmartx|ajuda|help|suporte|support|fechar|close|export|exportar|csv|excel|download|refresh|atualizar|imprimir|print|salvar|save|cancelar|cancel|confirmar|confirm|novo|new|criar|create)$/i;
                const UTIL_CONTEM = /smartx|ai insights|my applications|hub insight/i;
                const limpar = (t) => t.replace(/^[^a-zA-Z0-9À-ÿ]+/, '').trim();
                const PERIGO = /delete|exclu|remov|logout|sair/i;
                const RE_PERIODO = /^(1|3|6|12)m$|^[1-9][0-9]*[yY]$|^[1-9]w$|^(today|yesterday|week|month|year|semana|mes|ano)$/i;
                const vistos = new Set();
                const resultado = [];

                document.querySelectorAll('[role="tab"]').forEach(el => {
                    const rect = el.getBoundingClientRect();
                    const textoRaw = el.innerText?.trim().split('\\n')[0]?.trim() || '';
                    const texto = limpar(textoRaw);
                    if (!texto || texto.length > 60) return;
                    if (!/[a-zA-Z]/.test(texto)) return;
                    if (rect.width === 0 || rect.left <= CONTEUDO_MIN_X || rect.top <= CONTEUDO_MIN_Y) return;
                    if (PERIGO.test(texto) || UTIL.test(texto) || UTIL_CONTEM.test(texto) || RE_PERIODO.test(texto)) return;
                    if (vistos.has(texto)) return;
                    vistos.add(texto);
                    resultado.push({ texto, x: Math.round(rect.left + rect.width/2), y: Math.round(rect.top + rect.height/2) });
                });

                if (resultado.length === 0) {
                    document.querySelectorAll('button,[role="button"]').forEach(btn => {
                        const pai = btn.parentElement;
                        if (!pai) return;
                        const irmaos = Array.from(pai.children).filter(c => c.tagName === 'BUTTON' || c.getAttribute('role') === 'button');
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
                self.log.emit(f"      [{len(tabs)} tabs]")
                for tab in tabs:
                    page.mouse.click(tab["x"], tab["y"])
                    try:
                        page.wait_for_function(
                            """() => {
                                const main = document.querySelector('main,[role="main"],[class*="content"],[class*="page"]');
                                return main && main.innerText.trim().length > 20;
                            }""",
                            timeout=4000,
                        )
                    except Exception:
                        pass
                    _aguardar_render(page, timeout_extra=400)
                    if ainda_na_pagina():
                        snap(f"Tab_{tab['texto']}")
                    else:
                        recuperar_pagina()

            filtros = page.evaluate("""() => {
                const RE = /^(7|14|30|60|90|180|365)d?$|^(1|3|6|12)m$|^[12]?[yw]$|^(today|yesterday|week|month|year|semana|mes|ano|quarter|trimestre|dia)$/i;
                const vistos = new Set();
                return Array.from(document.querySelectorAll('button,[role="button"]')).map(el => {
                    const rect = el.getBoundingClientRect();
                    const texto = el.innerText?.trim().replace(/\\s+/g,' ') || '';
                    return { texto, x: Math.round(rect.left + rect.width/2), y: Math.round(rect.top + rect.height/2),
                             ok: rect.width > 0 && rect.height > 0 && rect.left > 200 && RE.test(texto) };
                }).filter(f => { if (!f.ok || vistos.has(f.texto)) return false; vistos.add(f.texto); return true; });
            }""")
            if filtros:
                self.log.emit(f"      [{len(filtros)} filtros]")
                for f in filtros:
                    page.mouse.click(f["x"], f["y"])
                    _aguardar_render(page, timeout_extra=400)
                    if ainda_na_pagina():
                        snap(f"Filtro_{f['texto']}")
                    else:
                        recuperar_pagina()

            expandaveis = page.evaluate("""() => {
                const vistos = new Set();
                return Array.from(document.querySelectorAll(
                    '[aria-expanded="false"][role="button"],button[aria-expanded="false"],' +
                    'h1[aria-expanded="false"],h2[aria-expanded="false"],h3[aria-expanded="false"],' +
                    '[aria-expanded="false"][class*="accordion"],[aria-expanded="false"][class*="collapse"]'
                )).map(el => {
                    const rect = el.getBoundingClientRect();
                    const texto = (el.innerText?.trim().split('\\n')[0]?.trim() || el.getAttribute('aria-label') || '').slice(0,60);
                    return { texto, x: Math.round(rect.left + rect.width/2), y: Math.round(rect.top + rect.height/2),
                             ok: rect.width > 0 && rect.height > 0 && rect.left > 200 && texto.length > 1 };
                }).filter(e => { if (!e.ok || vistos.has(e.texto)) return false; vistos.add(e.texto); return true; });
            }""")
            if expandaveis:
                self.log.emit(f"      [{len(expandaveis)} expansíveis]")
                for exp in expandaveis[:8]:
                    page.mouse.click(exp["x"], exp["y"])
                    _aguardar_render(page, timeout_extra=300)
                    if not ainda_na_pagina():
                        recuperar_pagina()
                        continue
                    snap(f"Expandido_{exp['texto']}")
                    page.mouse.click(exp["x"], exp["y"])
                    page.wait_for_timeout(300)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-renderer-backgrounding",
                        "--force-color-profile=srgb",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = browser.new_context(
                    storage_state=SESSION_FILE if os.path.exists(SESSION_FILE) else None,
                    viewport={"width": 1920, "height": 1080},
                )
                page = context.new_page()
                page.goto(f"{_base_url}/home", wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

                if "login" in page.url.lower():
                    self.log.emit("Fazendo login...")
                    autenticar(page)
                    context.storage_state(path=SESSION_FILE)
                else:
                    self.log.emit("Sessão ativa.")

                def app_atual(page):
                    return page.evaluate("""() => {
                        const headerEls = document.querySelectorAll(
                            'header *, [class*="topbar"] *, [class*="navbar"] *, [class*="breadcrumb"] *'
                        );
                        for (const el of headerEls) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0 || rect.top > 100) continue;
                            const t = el.innerText?.trim().split(/[·>\\n]/)[0]?.trim();
                            if (t && t.includes(' — ') && t.length < 60) return t;
                        }
                        const nav = document.querySelector('aside.sidebar nav') || document.querySelector('aside.sidebar') || document.querySelector('aside nav') || document.querySelector('aside') || document.querySelector('[class*="sidebar"] nav') || document.querySelector('[class*="sidebar"]');
                        const container = nav?.children[0];
                        if (container && container.children[0]) {
                            const linhas = (container.children[0].innerText || '')
                                .split('\\n')
                                .map(t => t.trim())
                                .filter(t => t.length > 2 && !t.includes('←') && !t.includes('All modules'));
                            if (linhas.length > 0) return linhas[0];
                        }
                        if (container && container.children.length > 1) {
                            const t = container.children[1]?.innerText?.trim().split('\\n')[0]?.trim();
                            if (t && t.length > 2) return t;
                        }
                        return '';
                    }""")

                _NAV_SEL = """
                    const SELS = ['aside.sidebar nav','aside.sidebar','aside nav','aside','[class*="sidebar"] nav','[class*="sidebar"]'];
                    let nav = null;
                    for (const s of SELS) { nav = document.querySelector(s); if (nav) break; }
                """

                def _capturar_rota(page, rota):
                    pasta_modulo = self.pasta_saida / slug(rota.strip("/"))
                    self.log.emit(f"\n{'='*40}")
                    self.log.emit(f"Módulo : {rota}")
                    self.log.emit(f"Saída  : {pasta_modulo.resolve()}")

                    navegar_modulo(page, rota)

                    if self.idioma:
                        self._trocar_idioma(page, self.idioma)

                    app_esperado = app_atual(page)
                    if app_esperado:
                        self.log.emit(f"Título do módulo: '{app_esperado}'")

                    grupos = page.evaluate(f"""() => {{
                        {_NAV_SEL}
                        if (!nav) return [];
                        const container = nav.children[0];
                        if (!container) return [];
                        const temGrupos = (parent, from) =>
                            Array.from(parent.children).slice(from).some(c =>
                                c.children[0]?.innerText?.trim().length > 1);
                        const startIdx = temGrupos(container, 1) ? 1 : 0;
                        return Array.from(container.children).slice(startIdx).map((grupo, i) => {{
                            const botao = grupo.children[0];
                            const textoRaw = botao?.innerText?.trim().split('\\n')[0] || '';
                            const texto = textoRaw.replace(/^[^\\w]+/, '').trim();
                            return {{ indice_no_container: i + startIdx, texto_limpo: texto }};
                        }}).filter(g => g.texto_limpo.length > 0);
                    }}""")

                    if not grupos:
                        self.log.emit("  AVISO: Nenhum grupo encontrado para esta rota.")
                        return

                    self.log.emit(f"\n{len(grupos)} grupos encontrados:")
                    for g in grupos:
                        self.log.emit(f"  • {g['texto_limpo']}")
                    self.log.emit("")

                    def _mesma_app(dest_app):
                        if not dest_app or not app_esperado:
                            return True
                        a, b = app_esperado.lower(), dest_app.lower()
                        return a in b or b in a

                    def _navegar_item_spa(page, idx, it) -> bool:
                        expandir_grupo(page, idx)
                        page.wait_for_timeout(200)
                        if not clicar_item(page, idx, it['indice']):
                            return False
                        _aguardar_render(page, timeout_extra=_EXTRA_WAIT + 500)
                        if 'login' in page.url.lower():
                            autenticar(page)
                        if app_esperado:
                            dest_app = app_atual(page)
                            if dest_app and not _mesma_app(dest_app):
                                return False
                        return True

                    def _recuperar_modulo(page, idx):
                        self.log.emit("  Recuperando navegação...")
                        navegar_modulo(page, rota)
                        page.wait_for_timeout(500)
                        expandir_grupo(page, idx)
                        page.wait_for_timeout(300)

                    _total_itens = 0
                    _itens_feitos = 0
                    idx_anterior = None

                    for num_grupo, grupo in enumerate(grupos, start=1):
                        nome_grupo = grupo['texto_limpo']
                        idx = grupo['indice_no_container']
                        pasta_grupo = pasta_modulo / f"{num_grupo:02d}_{slug(nome_grupo)}"

                        self.log.emit(f"[{num_grupo}/{len(grupos)}] {nome_grupo}")

                        if idx_anterior is not None:
                            fechar_grupo(page, idx_anterior)
                            page.wait_for_timeout(300)

                        navegar_modulo(page, rota)
                        page.wait_for_timeout(300)

                        if not expandir_grupo(page, idx):
                            self.log.emit("  Vazio.\n")
                            idx_anterior = idx
                            continue

                        itens = obter_itens(page, idx)
                        if not itens:
                            self.log.emit("  Nenhum item.\n")
                            idx_anterior = idx
                            continue

                        _total_itens += len(itens)
                        self.log.emit(f"  {len(itens)} itens")
                        self.progresso.emit(_itens_feitos, _total_itens)

                        for it in itens:
                            nome_item = it['texto']
                            pasta_pagina = pasta_grupo / f"{it['indice']+1:02d}_{slug(nome_item)}"
                            self.log.emit(f"  → {nome_item}")

                            try:
                                ok = _navegar_item_spa(page, idx, it)

                                if not ok:
                                    href = it.get('href', '')
                                    if not href:
                                        self.log.emit(f"  ✗ {nome_item} (sem href e clique falhou)")
                                        _itens_feitos += 1
                                        self.progresso.emit(_itens_feitos, _total_itens)
                                        continue
                                    dest = href if href.startswith('http') else f"{_base_url}{href}"
                                    page.goto(dest, wait_until="domcontentloaded", timeout=30000)
                                    _aguardar_render(page, timeout_extra=_EXTRA_WAIT)
                                    if 'login' in page.url.lower():
                                        autenticar(page)
                                    if app_esperado:
                                        dest_app = app_atual(page)
                                        if dest_app and not _mesma_app(dest_app):
                                            self.log.emit(f"  ⚠ {nome_item} pulado (módulo diferente: '{dest_app}')")
                                            _recuperar_modulo(page, idx)
                                            _itens_feitos += 1
                                            self.progresso.emit(_itens_feitos, _total_itens)
                                            continue
                                    expandir_grupo(page, idx)
                                    page.wait_for_timeout(300)

                                explorar_pagina(page, pasta_pagina,
                                                re_expandir=lambda i=idx: expandir_grupo(page, i))
                                self.log.emit(f"  ✓ {nome_item}")

                            except Exception as e:
                                self.log.emit(f"  ✗ {nome_item} (erro: {e})")
                                try:
                                    _recuperar_modulo(page, idx)
                                except Exception:
                                    pass

                            _itens_feitos += 1
                            self.progresso.emit(_itens_feitos, _total_itens)

                        idx_anterior = idx
                        self.log.emit("")

                    if self.gravar_video:
                        self.log.emit(f"\nCompondo vídeo: {rota}...")
                        saida = self._gerar_video_mp4(pasta_modulo)
                        if saida:
                            self.log.emit(f"  Vídeo salvo: {saida.name}")

                # ── Processa todas as rotas ────────────────────────────────────
                rotas = self.rotas
                self.log.emit(f"{len(rotas)} módulo(s) a capturar.\n")
                primeira_pasta = self.pasta_saida / slug(rotas[0].strip("/")) if rotas else self.pasta_saida

                for num_rota, rota in enumerate(rotas, start=1):
                    self.log.emit(f"\n▶ Rota {num_rota}/{len(rotas)}: {rota}")
                    try:
                        _capturar_rota(page, rota)
                    except Exception as e:
                        self.log.emit(f"  ✗ Erro na rota '{rota}': {e}")

                page.close()
                context.close()
                browser.close()

            self.log.emit("=" * 40)
            self.log.emit("Captura concluída com sucesso!")
            self.log.emit("=" * 40)
            self.concluido.emit(True, str(primeira_pasta))

        except Exception as e:
            self.log.emit(f"\nERRO: {e}")
            self.concluido.emit(False, "")

    def _trocar_idioma(self, page, idioma: str):
        CODIGO = idioma.upper()
        ORDEM = ["PT", "EN", "ES"]
        idx_alvo = ORDEM.index(CODIGO) if CODIGO in ORDEM else 0
        # Texto exato que aparece em cada opção do dropdown
        TEXTOS_OPCAO = {"PT": "br PT", "EN": "us EN", "ES": "es ES"}
        texto_opcao = TEXTOS_OPCAO.get(CODIGO, "br PT")

        try:
            self.log.emit(f"  Trocando idioma → {CODIGO}...")

            # Encontra o trigger ANTES de clicar (evaluate OK aqui — dropdown fechado)
            info = page.evaluate("""() => {
                const vw = window.innerWidth;
                let best = null;
                for (const el of document.querySelectorAll('*')) {
                    const r = el.getBoundingClientRect();
                    if (r.top > 60 || r.left < vw * 0.7 || r.width === 0) continue;
                    const txt = (el.innerText || '').trim();
                    if (txt.match(/[A-Z]{2}/) && txt.length < 80) {
                        const area = r.width * r.height;
                        if (!best || area < best.area)
                            best = { text: txt,
                                     tx: r.left + r.width / 2,
                                     ty: r.top + r.height / 2,
                                     area };
                    }
                }
                return best;
            }""")

            if not info:
                self.log.emit("  ⚠ Botão de idioma não encontrado.")
                return

            tx, ty = info['tx'], info['ty']
            self.log.emit(f"  Trigger: '{info['text'][:25]}' ({tx:.0f},{ty:.0f})")

            _api_urls = []
            def _on_req(req):
                url = req.url
                if any(k in url.lower() for k in
                       ['lang', 'locale', 'preference', 'setting', 'i18n', 'idiom', 'user']):
                    _api_urls.append(f"{req.method} {url[:120]}")
            page.on('request', _on_req)

            # Abre o dropdown
            page.mouse.click(tx, ty)
            page.wait_for_timeout(700)

            # --- Tentativa 1: locator por texto exato (não causa blur) ---
            _selecionou = False
            try:
                opt = page.get_by_text(texto_opcao, exact=True).first
                opt.click(timeout=2000)
                page.wait_for_timeout(600)
                if _api_urls:
                    self.log.emit(f"  API: {_api_urls[0]}")
                    _selecionou = True
            except Exception:
                pass

            # --- Tentativa 2: teclado Home + ArrowDown + Enter ---
            if not _selecionou:
                self.log.emit("  Locator falhou, tentando teclado...")
                page.mouse.click(tx, ty)
                page.wait_for_timeout(600)
                page.keyboard.press("Home")
                page.wait_for_timeout(200)
                for _ in range(idx_alvo):
                    page.keyboard.press("ArrowDown")
                    page.wait_for_timeout(150)
                page.keyboard.press("Enter")
                page.wait_for_timeout(800)
                if _api_urls:
                    self.log.emit(f"  API (teclado): {_api_urls[0]}")
                    _selecionou = True

            # --- Tentativa 3: cliques por coordenada em 4 gaps diferentes ---
            if not _selecionou:
                self.log.emit("  Teclado falhou, tentando coordenadas...")
                for gap in [40, 52, 65, 78]:
                    opt_y = ty + gap + idx_alvo * 36
                    _hit = []
                    def _on2(req, _h=_hit):
                        url = req.url
                        if any(k in url.lower() for k in
                               ['lang', 'locale', 'preference', 'setting', 'i18n', 'idiom', 'user']):
                            _h.append(url[:80])
                    page.on('request', _on2)
                    page.mouse.click(tx, ty)
                    page.wait_for_timeout(600)
                    page.mouse.click(tx, opt_y)
                    page.wait_for_timeout(500)
                    page.remove_listener('request', _on2)
                    if _hit:
                        self.log.emit(f"  API (gap={gap}): {_hit[0]}")
                        _selecionou = True
                        break

            page.remove_listener('request', _on_req)

            if not _selecionou:
                self.log.emit("  ⚠ Nenhuma API de idioma detectada em todas as tentativas")

            page.wait_for_timeout(500)
            page.reload(wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            self.log.emit(f"  ✓ Idioma: {CODIGO}")

        except Exception as e:
            self.log.emit(f"  ⚠ Falha ao trocar idioma: {e}")

    def _gerar_video_mp4(self, pasta_modulo: Path):
        import tempfile, shutil
        try:
            from PIL import Image, ImageDraw, ImageFont
            from imageio_ffmpeg import get_ffmpeg_exe
        except ImportError as e:
            self.log.emit(f"  Erro: {e}")
            return None

        W, H = 1920, 1080
        FPS = 24
        DUR_MOD  = 2.5
        DUR_GRUP = 1.5
        DUR_ITEM = 1.2
        DUR_FOTO = 2.5
        FADE     = 0.4

        _fonte_cache = {}

        def _fonte(tam):
            if tam in _fonte_cache:
                return _fonte_cache[tam]
            for caminho in [
                "C:/Windows/Fonts/segoeui.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]:
                try:
                    f = ImageFont.truetype(caminho, tam)
                    _fonte_cache[tam] = f
                    return f
                except Exception:
                    pass
            return ImageFont.load_default()

        def _card_png(destino: Path, linha1: str, linha2: str = "", cor=(10, 13, 30)):
            img = Image.new("RGB", (W, H), cor)
            d = ImageDraw.Draw(img)
            d.rectangle([(W//2 - 220, H//2 - 90), (W//2 + 220, H//2 - 85)], fill=(89, 140, 250))
            f1 = _fonte(68)
            bb = d.textbbox((0, 0), linha1, font=f1)
            d.text(((W - bb[2] + bb[0])//2, H//2 - 80), linha1, fill=(215, 222, 245), font=f1)
            if linha2:
                f2 = _fonte(34)
                bb2 = d.textbbox((0, 0), linha2, font=f2)
                d.text(((W - bb2[2] + bb2[0])//2, H//2 + 30), linha2, fill=(120, 138, 180), font=f2)
            img.save(str(destino))

        tmp = Path(tempfile.mkdtemp(prefix="sxbot_"))
        try:
            frames = []   # list of (path_str, duration)
            total = 0

            nome_mod = pasta_modulo.name.replace("-", " ").replace("_", " ").upper()
            p = tmp / "m.png"
            _card_png(p, nome_mod, "SmartXHub")
            frames.append((str(p), DUR_MOD))

            for gi, grupo_dir in enumerate(sorted(pasta_modulo.iterdir())):
                if not grupo_dir.is_dir():
                    continue
                nome_grupo = " ".join(grupo_dir.name.split("_")[1:]).upper()
                p = tmp / f"g{gi}.png"
                _card_png(p, nome_grupo, nome_mod, cor=(8, 10, 24))
                frames.append((str(p), DUR_GRUP))

                for ii, item_dir in enumerate(sorted(grupo_dir.iterdir())):
                    if not item_dir.is_dir():
                        continue
                    pngs = sorted(item_dir.glob("*.png"))
                    if not pngs:
                        continue
                    nome_item = " ".join(item_dir.name.split("_")[1:]).replace("_", " ")
                    p = tmp / f"i{gi}_{ii}.png"
                    _card_png(p, nome_item, nome_grupo, cor=(14, 19, 42))
                    frames.append((str(p), DUR_ITEM))
                    for png in pngs:
                        frames.append((str(png), DUR_FOTO))
                        total += 1

            if not frames:
                self.log.emit("  Nenhum screenshot encontrado.")
                return None

            N = len(frames)
            self.log.emit(f"  {total} screenshots · {N} frames · renderizando com xfade...")

            ffmpeg = get_ffmpeg_exe()

            # Entradas: cada imagem como -loop 1 -t DUR -i path
            inputs = []
            for path_str, dur in frames:
                inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", path_str]

            # filter_complex com xfade escrito em arquivo para evitar limite de linha
            filter_lines = []
            for i in range(N):
                filter_lines.append(
                    f"[{i}:v]scale={W}:{H}:flags=lanczos,setsar=1,fps={FPS}[v{i}]"
                )

            # offset do i-ésimo xfade = sum(D[0..i-1]) - i*FADE
            sum_dur = frames[0][1]
            prev_lbl = "v0"
            for i in range(1, N):
                offset = max(0.0, sum_dur - i * FADE)
                next_lbl = f"x{i}" if i < N - 1 else "out"
                filter_lines.append(
                    f"[{prev_lbl}][v{i}]xfade=transition=fade"
                    f":duration={FADE}:offset={offset:.3f}[{next_lbl}]"
                )
                prev_lbl = next_lbl
                sum_dur += frames[i][1]

            if N == 1:
                filter_lines.append("[v0]copy[out]")

            fscript = tmp / "flt.txt"
            fscript.write_text(";\n".join(filter_lines), encoding="utf-8")

            saida = pasta_modulo / "video_completo.mp4"
            if saida.exists():
                saida.unlink()

            cmd = (
                [ffmpeg, "-y"]
                + inputs
                + [
                    "-filter_complex_script", str(fscript),
                    "-map", "[out]",
                    "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                    "-pix_fmt", "yuv420p",
                    str(saida),
                ]
            )

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            if result.returncode != 0:
                self.log.emit(f"  Erro ffmpeg: {result.stderr[-400:]}")
                return None

            return saida

        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _verificar_playwright(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                path = p.chromium.executable_path
                if os.path.exists(path):
                    return True
        except Exception:
            pass
        self.log.emit("Iniciando dependências...")
        try:
            from playwright._impl._driver import compute_driver_executable, get_driver_env
            driver_executable, driver_cli = compute_driver_executable()
            env = get_driver_env()
            res = subprocess.run(
                [str(driver_executable), str(driver_cli), "install", "chromium"],
                capture_output=True, text=True, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if res.returncode == 0:
                return True
            self.log.emit(f"Erro ao instalar dependências: {res.stderr}")
            return False
        except Exception as e:
            self.log.emit(f"Erro: {e}")
            return False


# ── Estilos ────────────────────────────────────────────────────────────────────

STYLE = """
QMainWindow, QWidget#central {
    background-color: #1e1e2e;
}
QScrollBar:vertical {
    background: #1e1e2e; width: 6px; border: none;
}
QScrollBar::handle:vertical {
    background: #45475a; border-radius: 3px; min-height: 20px;
}
QScrollBar:horizontal {
    background: #1e1e2e; height: 6px; border: none;
}
QScrollBar::handle:horizontal {
    background: #45475a; border-radius: 3px; min-width: 20px;
}
QLabel#titulo {
    color: #cdd6f4;
    font-size: 17px;
    font-weight: bold;
    letter-spacing: 0.5px;
}
QLabel#subtitulo {
    color: #585b70;
    font-size: 10px;
}
QFrame#card {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 10px;
}
QLabel#section_title {
    color: #585b70;
    font-size: 9px;
    font-weight: bold;
    letter-spacing: 1.2px;
}
QLabel#field_label {
    color: #7f849c;
    font-size: 10px;
}
QLineEdit {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 11px;
}
QLineEdit:focus {
    border: 1px solid #89b4fa;
    background-color: #1a1a2e;
}
QCheckBox {
    color: #7f849c;
    font-size: 10px;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px; height: 14px;
    border-radius: 3px;
    border: 1px solid #45475a;
    background: #1e1e2e;
}
QCheckBox::indicator:checked {
    background: #89b4fa;
    border-color: #89b4fa;
}
QComboBox {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
    min-height: 28px;
}
QComboBox:focus { border-color: #89b4fa; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    selection-background-color: #313244;
    selection-color: #cdd6f4;
    outline: none;
}
QPushButton#btn_iniciar {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
    border-radius: 8px;
    padding: 11px;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 0.3px;
}
QPushButton#btn_iniciar:hover { background-color: #74c7ec; }
QPushButton#btn_iniciar:disabled { background-color: #313244; color: #585b70; }
QPushButton#btn_abrir {
    background-color: transparent;
    color: #7f849c;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 10px;
}
QPushButton#btn_abrir:hover { background-color: #313244; color: #cdd6f4; }
QProgressBar {
    background-color: #313244;
    border: none;
    border-radius: 3px;
    height: 5px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #89b4fa, stop:1 #74c7ec);
    border-radius: 3px;
}
QPushButton#log_header {
    background-color: #181825;
    color: #585b70;
    border: 1px solid #313244;
    border-radius: 8px 8px 0 0;
    padding: 6px 12px;
    font-size: 9px;
    font-weight: bold;
    letter-spacing: 1px;
    text-align: left;
}
QPushButton#log_header[expanded="false"] { border-radius: 8px; }
QPushButton#log_header:hover { color: #a6adc8; background-color: #1e1e2e; }
QTextEdit#log {
    background-color: #11111b;
    color: #a6adc8;
    border: 1px solid #313244;
    border-top: none;
    border-radius: 0 0 8px 8px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 10px;
    padding: 10px 12px;
}
"""


# ── Componentes auxiliares ─────────────────────────────────────────────────────

class ClickableLabel(QLabel):
    clicked = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def mousePressEvent(self, event):
        self.clicked.emit(self._path)
        super().mousePressEvent(event)


class ImageModal(QDialog):
    def __init__(self, paths: list, index: int, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._idx = index

        self.setWindowTitle("Visualizador")
        self.setModal(True)
        self.setMinimumSize(900, 600)
        self.resize(1200, 780)
        self.setStyleSheet(
            "QDialog { background: #0d0e17; }"
            "QLabel#img { background: #0d0e17; }"
            "QPushButton { background:#313244; color:#cdd6f4; border:none; border-radius:6px; padding: 6px 18px; font-size:13px; }"
            "QPushButton:hover { background:#45475a; }"
            "QPushButton:disabled { color:#45475a; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._lbl_nome = QLabel()
        self._lbl_nome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_nome.setStyleSheet("color:#6c7086; font-size:10px;")
        root.addWidget(self._lbl_nome)

        self._lbl_img = QLabel()
        self._lbl_img.setObjectName("img")
        self._lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_img.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._lbl_img)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._btn_prev = QPushButton("← Anterior")
        self._btn_prev.clicked.connect(self._prev)
        self._btn_next = QPushButton("Próximo →")
        self._btn_next.clicked.connect(self._next)
        self._lbl_pos = QLabel()
        self._lbl_pos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_pos.setStyleSheet("color:#6c7086; font-size:11px; min-width:80px;")
        btn_fechar = QPushButton("✕  Fechar")
        btn_fechar.clicked.connect(self.close)
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._lbl_pos)
        nav.addWidget(self._btn_next)
        nav.addStretch()
        nav.addWidget(btn_fechar)
        root.addLayout(nav)

        self._mostrar()

    def _mostrar(self):
        path = self._paths[self._idx]
        self._lbl_nome.setText(Path(path).name)
        pix = QPixmap(path)
        if not pix.isNull():
            disp = self._lbl_img.size()
            pix = pix.scaled(
                max(disp.width(), 800), max(disp.height(), 500),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._lbl_img.setPixmap(pix)
        total = len(self._paths)
        self._lbl_pos.setText(f"{self._idx + 1} / {total}")
        self._btn_prev.setEnabled(self._idx > 0)
        self._btn_next.setEnabled(self._idx < total - 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._mostrar()

    def _prev(self):
        if self._idx > 0:
            self._idx -= 1
            self._mostrar()

    def _next(self):
        if self._idx < len(self._paths) - 1:
            self._idx += 1
            self._mostrar()

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key.Key_Left:
            self._prev()
        elif k == Qt.Key.Key_Right:
            self._next()
        elif k in (Qt.Key.Key_Escape, Qt.Key.Key_Return):
            self.close()
        else:
            super().keyPressEvent(event)


# ── Janela Principal ───────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SmartXHub — Screenshot Bot")
        self.setMinimumSize(560, 620)
        self.resize(820, 760)
        self.setStyleSheet(STYLE)

        self._worker = None
        self._thread = None
        self._pasta_resultado = ""
        self._thumb_col = 0
        self._thumb_row = 0
        self._THUMB_COLS = 4
        self._thumb_paths = []

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        titulo = QLabel("SmartXHub Screenshot Bot")
        titulo.setObjectName("titulo")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(titulo)

        subtitulo = QLabel("Captura automática de todos os menus do módulo")
        subtitulo.setObjectName("subtitulo")
        subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitulo)

        def make_card(spacing=6):
            card = QFrame()
            card.setObjectName("card")
            vbox = QVBoxLayout(card)
            vbox.setContentsMargins(14, 10, 14, 10)
            vbox.setSpacing(spacing)
            return card, vbox

        def section_label(text):
            lbl = QLabel(text.upper())
            lbl.setObjectName("section_title")
            return lbl

        def field(parent_layout, label_text, placeholder="", senha=False):
            lbl = QLabel(label_text)
            lbl.setObjectName("field_label")
            parent_layout.addWidget(lbl)
            entry = QLineEdit()
            entry.setPlaceholderText(placeholder)
            entry.setFixedHeight(28)
            if senha:
                entry.setEchoMode(QLineEdit.EchoMode.Password)
            parent_layout.addWidget(entry)
            return entry

        # ── Card Configuração ──────────────────────────────────────────────────
        card_cfg, cfg_vbox = make_card()
        cfg_vbox.addWidget(section_label("Configuração"))

        row1 = QHBoxLayout()
        row1.setSpacing(10)

        col_url = QVBoxLayout()
        col_url.setSpacing(4)
        self.entry_url = field(col_url, "URL Base", "https://dash.smartxhub.cloud")
        row1.addLayout(col_url, 3)

        col_rota = QVBoxLayout()
        col_rota.setSpacing(4)
        self.entry_rota = field(col_rota, "Rota do módulo (ou deixe vazio para usar a lista abaixo)", "/wo-dashboard")
        row1.addLayout(col_rota, 2)

        col_idioma = QVBoxLayout()
        col_idioma.setSpacing(4)
        lbl_idioma = QLabel("Idioma")
        lbl_idioma.setObjectName("field_label")
        col_idioma.addWidget(lbl_idioma)
        self.combo_idioma = QComboBox()
        self.combo_idioma.setFixedHeight(28)
        IDIOMAS = [
            ("— Manter atual —", ""),
            ("🇧🇷 PT", "pt"),
            ("🇺🇸 EN", "en"),
            ("🇪🇸 ES", "es"),
        ]
        for label, code in IDIOMAS:
            self.combo_idioma.addItem(label, code)
        col_idioma.addWidget(self.combo_idioma)
        row1.addLayout(col_idioma, 1)
        cfg_vbox.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        col_user = QVBoxLayout()
        col_user.setSpacing(4)
        self.entry_usuario = field(col_user, "Usuário / E-mail", "carlos.ribeiro")
        row2.addLayout(col_user)
        col_pass = QVBoxLayout()
        col_pass.setSpacing(4)
        self.entry_senha = field(col_pass, "Senha", "••••••••", senha=True)
        row2.addLayout(col_pass)
        cfg_vbox.addLayout(row2)

        lbl_pasta = QLabel("Pasta de saída")
        lbl_pasta.setObjectName("field_label")
        cfg_vbox.addWidget(lbl_pasta)

        row_pasta = QHBoxLayout()
        row_pasta.setSpacing(6)
        self.entry_pasta = QLineEdit()
        self.entry_pasta.setPlaceholderText(str(SCREENSHOTS_ROOT))
        self.entry_pasta.setReadOnly(True)
        self.entry_pasta.setFixedHeight(28)
        self.entry_pasta.setStyleSheet(
            "QLineEdit { color: #585b70; }"
            "QLineEdit[populated='true'] { color: #cdd6f4; }"
        )
        row_pasta.addWidget(self.entry_pasta)
        btn_pasta = QPushButton("📂")
        btn_pasta.setObjectName("btn_abrir")
        btn_pasta.setFixedSize(40, 28)
        btn_pasta.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_pasta.clicked.connect(self._escolher_pasta)
        row_pasta.addWidget(btn_pasta)
        cfg_vbox.addLayout(row_pasta)

        sep_cfg = QFrame()
        sep_cfg.setFrameShape(QFrame.Shape.HLine)
        sep_cfg.setStyleSheet("color: #313244; margin: 2px 0;")
        cfg_vbox.addWidget(sep_cfg)

        self.chk_video = QCheckBox("Gerar vídeo dos screenshots (.mp4)")
        cfg_vbox.addWidget(self.chk_video)

        root.addWidget(card_cfg)

        # ── Card Lista de Módulos ──────────────────────────────────────────────
        card_mod, mod_vbox = make_card()
        mod_vbox.addWidget(section_label("Lista de Módulos"))

        lbl_mod_info = QLabel("Uma rota por linha  (ex: /wo-dashboard)  —  ou carregue um .txt")
        lbl_mod_info.setObjectName("field_label")
        mod_vbox.addWidget(lbl_mod_info)

        self.txt_rotas = QTextEdit()
        self.txt_rotas.setPlaceholderText(
            "/wo-dashboard\n/sd-dashboard\n/asset-management\n..."
        )
        self.txt_rotas.setFixedHeight(90)
        self.txt_rotas.setStyleSheet(
            "QTextEdit { background:#1e1e2e; color:#cdd6f4; border:1px solid #313244;"
            " border-radius:6px; padding:5px 9px; font-family:Consolas,monospace; font-size:11px; }"
            "QTextEdit:focus { border-color:#89b4fa; }"
        )
        mod_vbox.addWidget(self.txt_rotas)

        row_mod = QHBoxLayout()
        row_mod.setSpacing(6)
        btn_carregar_txt = QPushButton("📄  Carregar .txt")
        btn_carregar_txt.setObjectName("btn_abrir")
        btn_carregar_txt.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_carregar_txt.clicked.connect(self._carregar_txt_rotas)
        row_mod.addWidget(btn_carregar_txt)
        btn_limpar_rotas = QPushButton("✕  Limpar")
        btn_limpar_rotas.setObjectName("btn_abrir")
        btn_limpar_rotas.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_limpar_rotas.clicked.connect(self.txt_rotas.clear)
        row_mod.addWidget(btn_limpar_rotas)
        row_mod.addStretch()
        mod_vbox.addLayout(row_mod)

        root.addWidget(card_mod)

        # ── Botões ─────────────────────────────────────────────────────────────
        self.btn = QPushButton("▶   Iniciar Captura")
        self.btn.setObjectName("btn_iniciar")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self._iniciar)
        root.addWidget(self.btn)

        self.btn_abrir = QPushButton("📁  Abrir pasta de screenshots")
        self.btn_abrir.setObjectName("btn_abrir")
        self.btn_abrir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_abrir.clicked.connect(self._abrir_pasta)
        self.btn_abrir.hide()
        root.addWidget(self.btn_abrir)

        # ── Card Progresso + Galeria ───────────────────────────────────────────
        self._card_exec, exec_vbox = make_card()

        hprog = QHBoxLayout()
        hprog.setSpacing(10)
        self.lbl_progresso = QLabel("Iniciando...")
        self.lbl_progresso.setObjectName("field_label")
        hprog.addWidget(self.lbl_progresso)
        hprog.addStretch()
        self.lbl_pct = QLabel("0%")
        self.lbl_pct.setStyleSheet("color:#89b4fa; font-size:11px; font-weight:bold;")
        hprog.addWidget(self.lbl_pct)
        exec_vbox.addLayout(hprog)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        exec_vbox.addWidget(self.progress)

        sep_inner = QFrame()
        sep_inner.setFrameShape(QFrame.Shape.HLine)
        sep_inner.setStyleSheet("color: #313244;")
        exec_vbox.addWidget(sep_inner)

        gal_hdr = QHBoxLayout()
        gal_hdr.addWidget(section_label("Capturas"))
        gal_hdr.addStretch()
        self.lbl_thumb_count = QLabel("0 frames")
        self.lbl_thumb_count.setObjectName("field_label")
        gal_hdr.addWidget(self.lbl_thumb_count)
        exec_vbox.addLayout(gal_hdr)

        self._gal_scroll = QScrollArea()
        self._gal_scroll.setWidgetResizable(True)
        self._gal_scroll.setFixedHeight(136)
        self._gal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._gal_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._gal_scroll.setStyleSheet(
            "QScrollArea { background: #11111b; border: 1px solid #313244; border-radius: 6px; }"
        )
        self._gal_widget = QWidget()
        self._gal_widget.setStyleSheet("background: #11111b;")
        self._gal_layout = QGridLayout(self._gal_widget)
        self._gal_layout.setContentsMargins(8, 8, 8, 8)
        self._gal_layout.setSpacing(6)
        self._gal_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._gal_scroll.setWidget(self._gal_widget)
        exec_vbox.addWidget(self._gal_scroll)

        self._card_exec.hide()
        root.addWidget(self._card_exec)

        # ── Log colapsável ─────────────────────────────────────────────────────
        self.btn_log_header = QPushButton("▼  Log de execução")
        self.btn_log_header.setObjectName("log_header")
        self.btn_log_header.setCheckable(True)
        self.btn_log_header.setChecked(True)
        self.btn_log_header.setProperty("expanded", "true")
        self.btn_log_header.clicked.connect(self._toggle_log)
        root.addWidget(self.btn_log_header)

        self.log_area = QTextEdit()
        self.log_area.setObjectName("log")
        self.log_area.setReadOnly(True)
        self.log_area.setMinimumHeight(140)
        root.addWidget(self.log_area)

        # ── Carrega config ─────────────────────────────────────────────────────
        usuario_salvo, rota_salva, url_salva, pasta_salva, idioma_salvo = carregar_config()
        if usuario_salvo:
            self.entry_usuario.setText(usuario_salvo)
        self.entry_rota.setText(rota_salva)
        self.entry_url.setText(url_salva)
        if pasta_salva and Path(pasta_salva).exists():
            self.entry_pasta.setText(pasta_salva)
            self.entry_pasta.setProperty("populated", "true")
        if idioma_salvo:
            idx = self.combo_idioma.findData(idioma_salvo)
            if idx >= 0:
                self.combo_idioma.setCurrentIndex(idx)

    def _carregar_txt_rotas(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar arquivo de rotas", "", "Arquivos de texto (*.txt);;Todos os arquivos (*)"
        )
        if not path:
            return
        try:
            conteudo = Path(path).read_text(encoding="utf-8")
            self.txt_rotas.setPlainText(conteudo.strip())
        except Exception as e:
            QMessageBox.warning(self, "Erro", f"Não foi possível ler o arquivo:\n{e}")

    def _iniciar(self):
        usuario = self.entry_usuario.text().strip()
        senha   = self.entry_senha.text()
        rota_unica = self.entry_rota.text().strip()
        base_url = self.entry_url.text().strip() or BASE_URL

        # Monta lista de rotas: aceita "Nome    /rota" ou só "/rota"
        def _extrair_rota(linha: str):
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                return None
            # Divide por tab ou 2+ espaços e pega o token que começa com /
            import re as _re
            tokens = _re.split(r'\t| {2,}', linha)
            for tok in reversed(tokens):
                tok = tok.strip()
                if tok.startswith("/"):
                    return tok
            # Linha com só uma coluna sem /
            return ("/" + linha) if not linha.startswith("/") else linha

        rotas_txt = [r for r in (_extrair_rota(l) for l in self.txt_rotas.toPlainText().splitlines()) if r]
        if rota_unica:
            if not rota_unica.startswith("/"):
                rota_unica = "/" + rota_unica
            if rota_unica not in rotas_txt:
                rotas_txt.insert(0, rota_unica)

        if not usuario or not senha:
            QMessageBox.warning(self, "Atenção", "Preencha usuário e senha.")
            return
        if not rotas_txt:
            QMessageBox.warning(self, "Atenção", "Informe pelo menos uma rota do módulo.")
            return

        rota = rotas_txt[0]
        pasta_saida = self.entry_pasta.text().strip()
        idioma = self.combo_idioma.currentData()
        salvar_config(usuario, rota, base_url, pasta_saida, idioma)

        self.btn.setEnabled(False)
        self.btn.setText("⏳  Executando...")
        self.btn_abrir.hide()
        self.log_area.clear()
        self.log_area.setHtml('<body style="background:#11111b;font-family:Consolas,monospace;font-size:10px;"></body>')
        if not self.btn_log_header.isChecked():
            self.btn_log_header.setChecked(True)
            self._toggle_log(True)

        while self._gal_layout.count():
            item = self._gal_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._thumb_col = 0
        self._thumb_row = 0
        self._thumb_paths.clear()
        self.lbl_thumb_count.setText("0 frames")
        self.lbl_progresso.setText("Iniciando...")
        self.lbl_pct.setText("0%")
        self.lbl_pct.setStyleSheet("color:#89b4fa; font-size:11px; font-weight:bold;")
        self.progress.setValue(0)
        self._card_exec.show()

        self._thread = QThread()
        self._worker = CapturaWorker(
            usuario, senha, rotas_txt,
            gravar_video=self.chk_video.isChecked(),
            base_url=base_url,
            pasta_saida=pasta_saida,
            idioma=idioma,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._append_log)
        self._worker.concluido.connect(self._concluido)
        self._worker.concluido.connect(self._thread.quit)
        self._worker.progresso.connect(self._atualizar_progresso)
        self._worker.screenshot_capturado.connect(self._adicionar_thumb)
        self._thread.start()

    def _escolher_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self, "Selecionar pasta de saída",
            self.entry_pasta.text() or str(SCREENSHOTS_ROOT),
        )
        if pasta:
            self.entry_pasta.setText(pasta)
            self.entry_pasta.setProperty("populated", "true")
            self.entry_pasta.style().unpolish(self.entry_pasta)
            self.entry_pasta.style().polish(self.entry_pasta)

    def _toggle_log(self, checked: bool):
        self.log_area.setVisible(checked)
        self.btn_log_header.setText("▼  Log de execução" if checked else "▶  Log de execução")
        self.btn_log_header.setProperty("expanded", "true" if checked else "false")
        self.btn_log_header.style().unpolish(self.btn_log_header)
        self.btn_log_header.style().polish(self.btn_log_header)

    def _append_log(self, msg: str):
        from html import escape as _esc
        from datetime import datetime

        ts = f'<span style="color:#45475a;font-size:9px">{datetime.now().strftime("%H:%M:%S")}</span>'
        stripped = msg.strip()

        if stripped.startswith("===") or stripped.startswith("---"):
            html = '<hr style="border:none;border-top:1px solid #313244;margin:4px 0">'
        elif stripped.startswith("[") and "/" in stripped and "]" in stripped:
            html = f'<br>{ts} <span style="color:#89b4fa;font-weight:bold;font-size:11px">{_esc(msg)}</span>'
        elif stripped.startswith("→"):
            html = f'{ts} <span style="color:#89dceb">{_esc(msg)}</span>'
        elif stripped.startswith("✓"):
            html = f'{ts} <span style="color:#a6e3a1">{_esc(msg)}</span>'
        elif stripped.startswith("✗"):
            html = f'{ts} <span style="color:#f38ba8">{_esc(msg)}</span>'
        elif stripped.startswith("⚠"):
            html = f'{ts} <span style="color:#f9e2af">{_esc(msg)}</span>'
        elif stripped.endswith(".png") or stripped.endswith(".mp4"):
            html = f'<span style="color:#45475a;padding-left:16px">{"&nbsp;" * 6}📷 {_esc(stripped)}</span>'
        elif stripped.startswith("[") and stripped.endswith("]"):
            html = f'<span style="color:#585b70;font-style:italic">&nbsp;&nbsp;{_esc(msg)}</span>'
        elif any(stripped.startswith(p) for p in ("Módulo", "Saída", "Sessão", "Fazendo login", "Login OK")):
            html = f'{ts} <span style="color:#cba6f7">{_esc(msg)}</span>'
        elif stripped.upper().startswith("ERRO") or "erro" in stripped.lower()[:8]:
            html = f'{ts} <b><span style="color:#f38ba8">{_esc(msg)}</span></b>'
        elif stripped.startswith("Compondo") or stripped.startswith("Captura concluída"):
            html = f'{ts} <span style="color:#cba6f7;font-weight:bold">{_esc(msg)}</span>'
        elif stripped == "":
            html = "<br>"
        else:
            html = f'{ts} <span style="color:#7f849c">{_esc(msg)}</span>'

        cursor = self.log_area.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_area.setTextCursor(cursor)
        self.log_area.insertHtml(html + "<br>")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def _atualizar_progresso(self, atual: int, total: int):
        pct = int(atual / total * 100) if total else 0
        self.progress.setValue(pct)
        self.lbl_progresso.setText(f"{atual} de {total} itens")
        self.lbl_pct.setText(f"{pct}%")

    def _adicionar_thumb(self, path: str):
        THUMB_W, THUMB_H = 192, 108
        self._thumb_paths.append(path)
        idx = len(self._thumb_paths) - 1

        lbl = ClickableLabel(path)
        lbl.setFixedSize(THUMB_W, THUMB_H)
        lbl.setStyleSheet("border: 1px solid #313244; background: #0d0e17;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        pix = QPixmap(path)
        if not pix.isNull():
            pix = pix.scaled(THUMB_W, THUMB_H,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        lbl.setPixmap(pix)
        lbl.setToolTip(Path(path).name)
        lbl.clicked.connect(lambda p, i=idx: self._abrir_modal(i))

        self._gal_layout.addWidget(lbl, self._thumb_row, self._thumb_col)
        self._thumb_col += 1
        if self._thumb_col >= self._THUMB_COLS:
            self._thumb_col = 0
            self._thumb_row += 1

        count = len(self._thumb_paths)
        self.lbl_thumb_count.setText(f"{count} frame{'s' if count != 1 else ''}")
        sb = self._gal_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _abrir_modal(self, index: int):
        modal = ImageModal(list(self._thumb_paths), index, parent=self)
        modal.exec()

    def _concluido(self, sucesso: bool, pasta: str):
        self.btn.setEnabled(True)
        self.btn.setText("▶   Iniciar Captura")
        if sucesso:
            self.progress.setValue(100)
            self.lbl_progresso.setText("Concluído com sucesso")
            self.lbl_pct.setText("100%")
        else:
            self.lbl_progresso.setText("Encerrado com erro")
            self.lbl_pct.setStyleSheet("color:#f38ba8; font-size:11px; font-weight:bold;")
        self._pasta_resultado = pasta

        if sucesso:
            self.btn_abrir.show()
            QMessageBox.information(self, "Concluído!", f"Screenshots salvos em:\n{pasta}")
        else:
            QMessageBox.critical(self, "Erro", "Ocorreu um erro. Verifique o log.")

    def _abrir_pasta(self):
        if self._pasta_resultado and os.path.exists(self._pasta_resultado):
            os.startfile(self._pasta_resultado)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
