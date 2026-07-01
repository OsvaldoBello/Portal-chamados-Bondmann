#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador do protótipo de alta fidelidade — Portal de Chamados Bondmann Química.
Produz todas as telas como HTML estático (stack real: Tailwind + HTMX/Alpine + Chart.js),
usando os tokens oficiais da marca (Manual de Identidade Visual) e o Plano Mestre.

Rode:  python3 prototipos/build.py
Saída: arquivos .html em prototipos/
"""
import os, math, datetime

OUT = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------------
# 1. TOKENS DA MARCA  (Manual de Identidade Visual — PANTONE 2955C / 367C + sub-marcas)
# ----------------------------------------------------------------------------
TW_COLORS = """
navy: { 900:'#0E2747', 800:'#14315A', 700:'#1C3A63', DEFAULT:'#2E466F', 500:'#3D5A8A', 100:'#E7ECF3' },
brandgreen: { DEFAULT:'#ACC76B', 600:'#7FA53D', 700:'#5F8A2E', 100:'#EEF4E0' },
ink: '#1E293B', muted:'#64748B', faint:'#94A3B8',
surface:'#F4F6F9', surface2:'#EAEEF4', line:'#E2E8F0',
st_novo:'#2563EB', st_atend:'#6366F1', st_aguard:'#F59E0B', st_resolv:'#16A34A',
sla_ok:'#16A34A', sla_warn:'#F59E0B', sla_danger:'#DC2626',
pr_baixa:'#64748B', pr_media:'#2563EB', pr_alta:'#F59E0B', pr_urgente:'#DC2626',
ac_clean:'#1FB98C', ac_auto:'#41B6E6', ac_industry:'#5560B0', ac_fluid:'#E63E62', ac_max:'#F0934E', ac_service:'#1B8A8F'
"""

# ----------------------------------------------------------------------------
# 2. LOGO MOLECULAR (SVG) — 7 anéis em flor (1 central + 6 ao redor)
# ----------------------------------------------------------------------------
def _mark_circles():
    cx = cy = 23.0
    R = 12.5
    r = 5.3
    circles = [f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}"/>']
    for i in range(6):
        a = math.radians(-90 + i*60)
        circles.append(f'<circle cx="{cx + R*math.cos(a):.2f}" cy="{cy + R*math.sin(a):.2f}" r="{r}"/>')
    return "".join(circles)

MARK = ('<svg viewBox="0 0 46 46" fill="none" stroke="currentColor" stroke-width="2.4" '
        'class="{cls}">' + _mark_circles() + '</svg>')

def logo(variant="dark", size="text-[22px]"):
    word = "#FFFFFF" if variant == "dark" else "#2E466F"
    mark = MARK.format(cls="w-9 h-9 text-brandgreen shrink-0")
    return f'''<div class="flex items-center gap-2.5 select-none">
      {mark}
      <div class="leading-none">
        <div class="font-display font-black tracking-tight {size}" style="color:{word}">BONDMANN</div>
        <div class="font-medium tracking-[0.42em] text-[9px] mt-0.5" style="color:{word};opacity:.92">QUÍMICA</div>
      </div>
    </div>'''

# ----------------------------------------------------------------------------
# 3. ÍCONES (heroicons outline, 24x24)
# ----------------------------------------------------------------------------
ICONS = {
 "dashboard":"M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z",
 "ticket":"M16.5 6v.75m0 3v.75m0 3v.75m0 3V18m-9-5.25h5.25M7.5 15h3M3.375 5.25c-.621 0-1.125.504-1.125 1.125v3.026a3 3 0 010 5.198v3.026c0 .621.504 1.125 1.125 1.125h17.25c.621 0 1.125-.504 1.125-1.125v-3.026a3 3 0 010-5.198V6.375c0-.621-.504-1.125-1.125-1.125H3.375z",
 "plus":"M12 9v6m3-3H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z",
 "inbox":"M2.25 13.5h3.86a2.25 2.25 0 012.012 1.244l.256.512a2.25 2.25 0 002.013 1.244h3.218a2.25 2.25 0 002.013-1.244l.256-.512a2.25 2.25 0 012.013-1.244h3.859M2.25 13.84V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18v-4.16c0-.224-.034-.447-.1-.661L19.24 5.34a2.25 2.25 0 00-2.15-1.59H6.911a2.25 2.25 0 00-2.15 1.59L2.35 13.18a2.25 2.25 0 00-.1.66z",
 "columns":"M9 4.5v15m6-15v15m-10.875 0h15.75c.621 0 1.125-.504 1.125-1.125V5.625c0-.621-.504-1.125-1.125-1.125H4.125C3.504 4.5 3 5.004 3 5.625v12.75c0 .621.504 1.125 1.125 1.125z",
 "building":"M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21",
 "clock":"M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z",
 "tag":"M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.1 18.1 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z",
 "users":"M15 19.128a9.38 9.38 0 002.625.372 9.34 9.34 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.32 12.32 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z",
 "chart":"M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z",
 "bell":"M14.857 17.082a23.85 23.85 0 005.454-1.31A8.97 8.97 0 0118 9.75V9A6 6 0 006 9v.75a8.97 8.97 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.26 24.26 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0",
 "search":"M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z",
 "clip":"M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13",
 "logout":"M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15M12 9l-3 3m0 0l3 3m-3-3h12.75",
 "send":"M6 12L3.269 3.126A59.77 59.77 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5",
 "lock":"M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z",
 "mail":"M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75",
 "user":"M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.93 17.93 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z",
 "funnel":"M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 01-.659 1.591l-5.432 5.432a2.25 2.25 0 00-.659 1.591v2.927a2.25 2.25 0 01-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 00-.659-1.591L3.659 7.409A2.25 2.25 0 013 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0112 3z",
 "download":"M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5",
 "check":"M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
 "chevright":"M8.25 4.5l7.5 7.5-7.5 7.5",
 "dots":"M6.75 12a.75.75 0 11-1.5 0 .75.75 0 011.5 0zM12.75 12a.75.75 0 11-1.5 0 .75.75 0 011.5 0zM18.75 12a.75.75 0 11-1.5 0 .75.75 0 011.5 0z",
 "edit":"M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zM19.5 7.125L16.875 4.5",
 "edit2":"M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z",
 "trend":"M2.25 18L9 11.25l4.306 4.307a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941",
 "calendar":"M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5",
 "shield":"M9 12.75L11.25 15 15 9.75m-3-7.036A11.96 11.96 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z",
 "doc":"M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z",
 "menu":"M3.75 6.75h16.5M3.75 12h16.5M3.75 17.25h16.5",
 "cog":"M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.03 7.03 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.93 6.93 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.281z M15 12a3 3 0 11-6 0 3 3 0 016 0z",
 "home":"M2.25 12l8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25",
}

def icon(name, cls="w-5 h-5", sw="1.6"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="{ICONS[name]}"/></svg>')

# ----------------------------------------------------------------------------
# 4. SHELL / HEAD
# ----------------------------------------------------------------------------
HEAD = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · Bondmann Química</title>
<link rel="stylesheet" href="./assets/fonts.css">
<link rel="stylesheet" href="./assets/app.css">
<script defer src="./assets/alpine.min.js"></script>
__EXTRA__
<style>
  html{scroll-behavior:smooth}
  [x-cloak]{display:none!important}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:8px;border:2px solid transparent;background-clip:content-box}
  ::-webkit-scrollbar-thumb:hover{background:#94a3b8;background-clip:content-box}
  .glass{backdrop-filter:saturate(1.2) blur(2px)}
</style>
</head>
<body class="font-sans bg-surface text-ink antialiased">
"""

def head(title, extra=""):
    return HEAD.replace("__TITLE__", title).replace("__COLORS__", TW_COLORS).replace("__EXTRA__", extra)

FOOT = "</body></html>"

# ---- chrome ----------------------------------------------------------------
NAV = {
 "cliente": [
   ("cliente-dashboard.html","dashboard","Painel"),
   ("cliente-novo-chamado.html","plus","Abrir chamado"),
   ("cliente-dashboard.html","ticket","Meus chamados"),
 ],
 "operador": [
   ("operador-fila-lista.html","inbox","Fila de chamados"),
   ("operador-fila-kanban.html","columns","Kanban"),
   ("operador-atendimento.html","ticket","Atendimento"),
   ("admin-dashboard.html","chart","Indicadores"),
 ],
 "admin": [
   ("admin-dashboard.html","dashboard","Dashboard"),
   ("operador-fila-lista.html","ticket","Chamados"),
   ("admin-categorias.html","tag","Categorias"),
   ("admin-usuarios.html","users","Usuários"),
   ("admin-relatorios.html","chart","Relatórios"),
 ],
}
ROLE_LABEL = {"cliente":"Cliente","operador":"Operador","admin":"Administrador"}

def sidebar(role, active):
    items = ""
    for href, ic, label in NAV[role]:
        on = (href == active)
        cls = ("bg-white/10 text-white" if on else "text-white/70 hover:text-white hover:bg-white/5")
        bar = '<span class="absolute left-0 top-1.5 bottom-1.5 w-1 rounded-r bg-brandgreen"></span>' if on else ''
        items += (f'<a href="{href}" class="relative flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition {cls}">'
                  f'{bar}{icon(ic,"w-5 h-5")}<span>{label}</span></a>')
    return f'''<aside class="w-[248px] shrink-0 bg-navy-900 min-h-screen flex flex-col fixed lg:sticky top-0 self-start z-40 transition-transform -translate-x-full lg:translate-x-0" :class="{{'translate-x-0':sidebar}}">
      <div class="px-5 h-16 flex items-center justify-between border-b border-white/10">{logo("dark","text-[19px]")}
        <button @click="sidebar=false" class="lg:hidden text-white/60 hover:text-white">✕</button></div>
      <nav class="flex-1 p-3 space-y-1 overflow-y-auto">{items}</nav>
      <div class="p-3 border-t border-white/10">
        <a href="login.html" class="flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-sm font-medium text-white/70 hover:text-white hover:bg-white/5 transition">{icon("logout")}<span>Sair</span></a>
      </div>
    </aside>'''

AVATARS = {"cliente":("MR","Marina Rocha","ac_auto"),"operador":("CF","Carlos Freitas","ac_industry"),"admin":("AB","Ana Bondmann","brandgreen")}

def topbar(role, title, subtitle="", actions=""):
    ini, name, color = AVATARS[role]
    notifs = [
      ("sla_danger","BOND-2026-00409 venceu o SLA","Agro Vale Verde · há 35m"),
      ("st_novo","Novo chamado atribuído a você","Metalúrgica Horizonte · há 1h"),
      ("st_resolv","Chamado BOND-2026-00405 resolvido","TecnoMetal · há 3h"),
    ]
    nrows = ""
    for c,t,m in notifs:
        nrows += (f'<a class="flex gap-3 px-4 py-3 hover:bg-surface border-b border-line/70 last:border-0">'
                  f'<span class="mt-1.5 w-2 h-2 rounded-full shrink-0" style="background:{_hex(c)}"></span>'
                  f'<div><div class="text-sm font-medium text-navy leading-snug">{t}</div><div class="text-xs text-muted mt-0.5">{m}</div></div></a>')
    menu_item = lambda ic,label,href: f'<a href="{href}" class="flex items-center gap-2.5 px-4 py-2.5 text-sm text-ink hover:bg-surface">{icon(ic,"w-4 h-4")}{label}</a>'
    return f'''<header class="h-16 bg-white border-b border-line sticky top-0 z-20 flex items-center gap-3 px-5 sm:px-7">
      <button @click="sidebar=true" class="lg:hidden w-10 h-10 -ml-2 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy">{icon("menu")}</button>
      <div class="min-w-0">
        <h1 class="font-display font-bold text-[17px] text-navy leading-tight truncate">{title}</h1>
        {f'<p class="text-xs text-muted truncate">{subtitle}</p>' if subtitle else ''}
      </div>
      <div class="flex-1"></div>
      {actions}
      <div class="relative" @click.outside="notif=false">
        <button @click="notif=!notif" class="relative w-10 h-10 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy transition">{icon("bell")}
          <span class="absolute top-2 right-2 w-2 h-2 rounded-full bg-sla_danger ring-2 ring-white"></span></button>
        <div x-show="notif" x-cloak x-transition.origin.top.right class="absolute right-0 mt-2 w-80 bg-white rounded-xl shadow-card border border-line overflow-hidden z-30">
          <div class="px-4 py-3 border-b border-line flex items-center justify-between"><span class="font-display font-bold text-sm text-navy">Notificações</span><span class="text-[11px] font-semibold text-brandgreen-700">3 novas</span></div>
          {nrows}
          <a class="block text-center text-sm font-semibold text-navy py-2.5 hover:bg-surface">Ver todas</a>
        </div>
      </div>
      <div class="relative pl-2 sm:pl-3 border-l border-line" @click.outside="user=false">
        <button @click="user=!user" class="flex items-center gap-3">
          <div class="text-right leading-tight hidden sm:block">
            <div class="text-sm font-semibold text-navy">{name}</div>
            <div class="text-[11px] text-muted">{ROLE_LABEL[role]}</div>
          </div>
          <div class="w-9 h-9 rounded-full grid place-items-center text-white text-sm font-bold" style="background:{_color(color)}">{ini}</div>
        </button>
        <div x-show="user" x-cloak x-transition.origin.top.right class="absolute right-0 mt-2 w-52 bg-white rounded-xl shadow-card border border-line overflow-hidden z-30 py-1">
          <div class="px-4 py-2 border-b border-line mb-1"><div class="text-sm font-semibold text-navy">{name}</div><div class="text-[11px] text-muted">{ROLE_LABEL[role]}</div></div>
          {menu_item("user","Meu perfil","perfil.html")}
          {menu_item("cog","Configurações","perfil.html")}
          <div class="border-t border-line mt-1 pt-1">{menu_item("logout","Sair","login.html")}</div>
        </div>
      </div>
    </header>'''

def _color(tok):
    m = {"ac_auto":"#41B6E6","ac_industry":"#5560B0","brandgreen":"#7FA53D","ac_clean":"#1FB98C"}
    return m.get(tok, "#7FA53D")

def shell(role, active, title, content, subtitle="", actions="", extra=""):
    return (head(title, extra) +
            f'<div class="flex min-h-screen" x-data="{{ sidebar:false, notif:false, user:false }}">'
            f'{sidebar(role, active)}'
            f'<div x-show="sidebar" x-cloak @click="sidebar=false" class="fixed inset-0 bg-navy-900/50 z-30 lg:hidden"></div>'
            f'<div class="flex-1 min-w-0 flex flex-col">{topbar(role, title, subtitle, actions)}'
            f'<main class="flex-1 p-5 sm:p-7 max-w-[1400px] w-full">{content}</main></div></div>' + FOOT)

# ---- átomos ----------------------------------------------------------------
STATUS = {
 "NOVO":("st_novo","Novo"), "EM_ATENDIMENTO":("st_atend","Em atendimento"),
 "AGUARDANDO":("st_aguard","Aguardando cliente"), "RESOLVIDO":("st_resolv","Resolvido"),
}
def status_badge(s):
    c,l = STATUS[s]
    return (f'<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold" '
            f'style="background:{_hex(c)}1a;color:{_hex(c)}"><span class="w-1.5 h-1.5 rounded-full" style="background:{_hex(c)}"></span>{l}</span>')

PRIOR = {"BAIXA":("pr_baixa","Baixa"),"MEDIA":("pr_media","Média"),"ALTA":("pr_alta","Alta"),"URGENTE":("pr_urgente","Urgente")}
def priority_badge(p):
    c,l = PRIOR[p]
    return (f'<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold" '
            f'style="background:{_hex(c)}14;color:{_hex(c)}">{l}</span>')

def _hex(tok):
    table = {"st_novo":"#2563EB","st_atend":"#6366F1","st_aguard":"#F59E0B","st_resolv":"#16A34A",
     "sla_ok":"#16A34A","sla_warn":"#F59E0B","sla_danger":"#DC2626",
     "pr_baixa":"#64748B","pr_media":"#2563EB","pr_alta":"#F59E0B","pr_urgente":"#DC2626"}
    return table[tok]

def sla_chip(state, text):
    # state: ok / warn / danger
    c = {"ok":"sla_ok","warn":"sla_warn","danger":"sla_danger"}[state]
    pulse = " animate-pulseDanger" if state == "danger" else ""
    return (f'<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-semibold{pulse}" '
            f'style="background:{_hex(c)}14;color:{_hex(c)}">{icon("clock","w-3.5 h-3.5","2")}{text}</span>')

def btn(label, kind="primary", ic=None, cls="", attrs=""):
    base = "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-semibold transition px-4 py-2.5"
    styles = {
      "primary":"bg-navy text-white hover:bg-navy-700 shadow-soft",
      "green":"bg-brandgreen-600 text-white hover:bg-brandgreen-700 shadow-soft",
      "ghost":"bg-white text-navy ring-1 ring-line hover:bg-surface",
      "subtle":"bg-surface2 text-navy hover:bg-line",
      "danger":"bg-sla_danger text-white hover:opacity-90",
    }
    i = icon(ic,"w-4 h-4","2") if ic else ""
    return f'<button {attrs} class="{base} {styles[kind]} {cls}">{i}{label}</button>'

def modal(state, title, body, footer):
    return f'''<div x-show="{state}" x-cloak class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div x-show="{state}" x-transition.opacity @click="{state}=false" class="absolute inset-0 bg-navy-900/60"></div>
      <div x-show="{state}" x-transition.scale.origin.center class="relative w-full max-w-lg bg-white rounded-2xl shadow-card overflow-hidden">
        <div class="flex items-center justify-between px-6 py-4 border-b border-line">
          <h3 class="font-display font-bold text-lg text-navy">{title}</h3>
          <button @click="{state}=false" class="w-8 h-8 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy">✕</button>
        </div>
        <div class="p-6 space-y-4">{body}</div>
        <div class="flex justify-end gap-2 px-6 py-4 bg-surface/60 border-t border-line">{footer}</div>
      </div>
    </div>'''

def avatar(ini, color="#5560B0", size="w-9 h-9 text-sm"):
    return f'<div class="{size} rounded-full grid place-items-center text-white font-bold shrink-0" style="background:{color}">{ini}</div>'

def card(inner, cls=""):
    return f'<div class="bg-white rounded-xl shadow-card border border-line/60 {cls}">{inner}</div>'

def field(label, control, hint=""):
    hint_html = f'<span class="block text-xs text-muted mt-1">{hint}</span>' if hint else ""
    return (f'<label class="block"><span class="block text-[13px] font-semibold text-navy mb-1.5">{label}</span>'
            f'{control}{hint_html}</label>')

def input_ctl(placeholder="", ic=None, value="", type="text"):
    pad = "pl-10" if ic else "pl-3.5"
    ichtml = f'<span class="absolute left-3 top-1/2 -translate-y-1/2 text-faint">{icon(ic,"w-5 h-5")}</span>' if ic else ""
    return (f'<div class="relative">{ichtml}<input type="{type}" value="{value}" placeholder="{placeholder}" '
            f'class="w-full {pad} pr-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm placeholder:text-faint"></div>')

def kpi(title, value, delta, ic, accent="#7FA53D", up=True):
    arrow = "M4.5 15.75l7.5-7.5 7.5 7.5" if up else "M19.5 8.25l-7.5 7.5-7.5-7.5"
    dcol = "#16A34A" if up else "#DC2626"
    return card(f'''<div class="p-5">
      <div class="flex items-start justify-between">
        <div class="w-10 h-10 rounded-lg grid place-items-center" style="background:{accent}1f;color:{accent}">{icon(ic,"w-5 h-5","1.8")}</div>
        <span class="inline-flex items-center gap-1 text-xs font-semibold" style="color:{dcol}">
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="{arrow}"/></svg>{delta}</span>
      </div>
      <div class="mt-3 text-[28px] font-display font-bold text-navy leading-none">{value}</div>
      <div class="mt-1 text-[13px] text-muted">{title}</div>
    </div>''')

# ----------------------------------------------------------------------------
# 5. DADOS DE EXEMPLO
# ----------------------------------------------------------------------------
TICKETS = [
  ("BOND-2026-00412","Falha no dosador da linha 3","Indústria Petroquímica Sul","EM_ATENDIMENTO","ALTA","warn","2h 10m restantes","CF"),
  ("BOND-2026-00411","Solicitação de FISPQ — FLUID B90","TecnoMetal Usinagem","AGUARDANDO","MEDIA","ok","1d 4h restantes","CF"),
  ("BOND-2026-00409","Vazamento em bombona — lote 8841","Agro Vale Verde","NOVO","URGENTE","danger","Vencido há 35m","—"),
  ("BOND-2026-00408","Dúvida de diluição — BD CLEAN","Frigorífico Boi Dourado","EM_ATENDIMENTO","MEDIA","ok","6h 20m restantes","MS"),
  ("BOND-2026-00405","Troca de plano SLA Bronze→Ouro","TecnoMetal Usinagem","RESOLVIDO","BAIXA","ok","Concluído no prazo","CF"),
  ("BOND-2026-00402","Produto chegou com avaria","Metalúrgica Horizonte","NOVO","ALTA","warn","3h 45m restantes","—"),
]

# ============================================================================
#  TELAS
# ============================================================================
PAGES = {}

# ---- AUTH shell ----
def auth_page(title, panel):
    body = f'''<div class="min-h-screen grid lg:grid-cols-2">
      <!-- brand side -->
      <div class="relative hidden lg:flex flex-col justify-between p-12 overflow-hidden bg-navy-900">
        <div class="absolute -left-24 -top-24 w-[420px] h-[420px] rounded-full border-[60px] border-white/5"></div>
        <div class="absolute right-[-120px] bottom-[-120px] w-[380px] h-[380px] rounded-full border-[48px] border-white/5"></div>
        <div class="absolute left-1/3 top-1/4 w-40 h-40 rounded-full border-[26px] border-brandgreen/10"></div>
        {logo("dark","text-2xl")}
        <div class="relative">
          <h2 class="font-display font-bold text-white text-[34px] leading-tight">Portal de<br>Chamados</h2>
          <p class="text-white/70 mt-4 max-w-sm leading-relaxed">Suporte técnico, SLA e atendimento das soluções químicas Bondmann — em um só lugar, com a agilidade que sua operação exige.</p>
          <div class="mt-8 flex gap-2.5">
            <span class="px-3 py-1 rounded-full text-[11px] font-semibold bg-white/10 text-white">BD CLEAN</span>
            <span class="px-3 py-1 rounded-full text-[11px] font-semibold bg-white/10 text-white">BD FLUID</span>
            <span class="px-3 py-1 rounded-full text-[11px] font-semibold bg-white/10 text-white">BD INDUSTRY</span>
            <span class="px-3 py-1 rounded-full text-[11px] font-semibold bg-white/10 text-white">BD MAX</span>
          </div>
        </div>
        <div class="relative h-1.5 w-full rounded-full bg-gradient-to-r from-brandgreen via-brandgreen-600 to-navy-500"></div>
      </div>
      <!-- form side -->
      <div class="flex items-center justify-center p-6 sm:p-12 bg-white">
        <div class="w-full max-w-[400px]">
          <div class="lg:hidden mb-8">{logo("light","text-xl")}</div>
          {panel}
          <p class="mt-10 text-center text-xs text-faint">© 2026 Bondmann Química · bondmann.com.br</p>
        </div>
      </div>
    </div>'''
    return head(title) + body + FOOT

# 1. LOGIN
PAGES["login.html"] = auth_page("Entrar", f'''
  <h1 class="font-display font-bold text-2xl text-navy">Acesse sua conta</h1>
  <p class="text-muted text-sm mt-1.5">Bem-vindo de volta. Informe seus dados para continuar.</p>
  <form class="mt-8 space-y-4">
    {field("E-mail corporativo", input_ctl("voce@empresa.com.br","mail",type="email"))}
    {field("Senha", input_ctl("••••••••••","lock",type="password"))}
    <div class="flex items-center justify-between text-sm">
      <label class="flex items-center gap-2 text-muted"><input type="checkbox" class="rounded border-line text-navy focus:ring-navy-500"> Manter conectado</label>
      <a href="recuperar-senha.html" class="font-semibold text-brandgreen-700 hover:underline">Esqueci a senha</a>
    </div>
    {btn("Entrar no portal","primary","logout","w-full")}
  </form>
  <p class="mt-7 text-center text-xs text-muted">Sistema interno da Bondmann Química. O acesso é provisionado pela administração.</p>
''')

# 2. CADASTRO
PAGES["cadastro.html"] = auth_page("Cadastro", f'''
  <h1 class="font-display font-bold text-2xl text-navy">Solicitar acesso</h1>
  <p class="text-muted text-sm mt-1.5">Crie sua conta de cliente para abrir e acompanhar chamados.</p>
  <form class="mt-7 space-y-4">
    {field("Nome completo", input_ctl("Seu nome","user"))}
    {field("E-mail corporativo", input_ctl("voce@empresa.com.br","mail",type="email"))}
    {field("Empresa", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Selecione sua empresa…</option><option>TecnoMetal Usinagem</option><option>Indústria Petroquímica Sul</option><option>Agro Vale Verde</option></select>',"Não encontrou? Fale com o administrador da sua conta.")}
    {field("Senha", input_ctl("Crie uma senha forte","lock",type="password"),"Mínimo 8 caracteres, com letras e números.")}
    <label class="flex items-start gap-2 text-xs text-muted"><input type="checkbox" class="mt-0.5 rounded border-line text-navy focus:ring-navy-500"> Li e aceito os termos de uso e a política de privacidade da Bondmann Química.</label>
    {btn("Criar conta","green","check","w-full")}
  </form>
  <p class="mt-7 text-center text-sm text-muted">Já tem conta?
    <a href="login.html" class="font-semibold text-navy hover:underline">Entrar</a></p>
''')

# 3. RECUPERAR SENHA
PAGES["recuperar-senha.html"] = auth_page("Recuperar senha", f'''
  <a href="login.html" class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-navy mb-6"><svg class="w-4 h-4 rotate-180" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="{ICONS['chevright']}"/></svg> Voltar ao login</a>
  <div class="w-12 h-12 rounded-xl bg-brandgreen-100 text-brandgreen-700 grid place-items-center mb-4">{icon("lock","w-6 h-6")}</div>
  <h1 class="font-display font-bold text-2xl text-navy">Recuperar senha</h1>
  <p class="text-muted text-sm mt-1.5">Informe seu e-mail e enviaremos um link seguro para você redefinir sua senha.</p>
  <form class="mt-7 space-y-4">
    {field("E-mail corporativo", input_ctl("voce@empresa.com.br","mail",type="email"))}
    {btn("Enviar link de recuperação","primary","send","w-full")}
  </form>
  <div class="mt-6 rounded-lg bg-feedback-info-bg text-st_novo text-xs p-3 flex gap-2" style="background:#EFF6FF">
    {icon("shield","w-4 h-4 shrink-0 mt-0.5","2")}<span>O link expira em 1 hora por segurança. Se não chegar, verifique a caixa de spam.</span>
  </div>
''')

# ============================================================================
#  PORTAL DO CLIENTE
# ============================================================================
def ticket_row(t, role="cliente"):
    code,title,emp,st,pr,sla,slatxt,op = t
    href = "cliente-chamado-detalhe.html" if role=="cliente" else "operador-atendimento.html"
    empcell = f'<td class="px-4 py-3.5 text-sm text-muted">{emp}</td>' if role!="cliente" else ""
    opcell = ""
    if role!="cliente":
        op_html = avatar(op,"#5560B0","w-7 h-7 text-[11px]") if op!="—" else '<span class="text-faint text-sm">Sem operador</span>'
        opcell = f'<td class="px-4 py-3.5">{op_html}</td>'
    sel = '<td class="pl-4 py-3.5"><input type="checkbox" class="rounded border-line text-navy focus:ring-navy-500"></td>' if role!="cliente" else ''
    return f'''<tr class="border-t border-line hover:bg-surface/60 transition">
      {sel}
      <td class="px-4 py-3.5"><a href="{href}" class="font-mono text-[12px] font-semibold text-brandgreen-700 hover:underline">{code}</a></td>
      <td class="px-4 py-3.5"><a href="{href}" class="text-sm font-medium text-navy hover:underline">{title}</a></td>
      {empcell}
      <td class="px-4 py-3.5">{status_badge(st)}</td>
      <td class="px-4 py-3.5">{priority_badge(pr)}</td>
      <td class="px-4 py-3.5">{sla_chip(sla,slatxt)}</td>
      {opcell}
      <td class="px-4 py-3.5 text-right pr-5"><button class="text-faint hover:text-navy">{icon("dots","w-5 h-5")}</button></td>
    </tr>'''

def table(headers, rows, role="cliente"):
    sel_h = '<th class="pl-4 py-3 w-10"><input type="checkbox" class="rounded border-line text-navy focus:ring-navy-500"></th>' if role!="cliente" else ''
    ths = "".join(f'<th class="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-muted">{h}</th>' for h in headers)
    return card(f'''<div class="overflow-x-auto"><table class="w-full">
      <thead class="bg-surface/70">{sel_h}{ths}<th></th></thead>
      <tbody>{rows}</tbody></table></div>''',"overflow-hidden")

# CLIENTE — DASHBOARD
cli_rows = "".join(ticket_row(t,"cliente") for t in TICKETS[:5])
cli_content = f'''
  <div class="rounded-xl p-6 mb-6 bg-navy-900 text-white relative overflow-hidden">
    <div class="absolute right-6 -top-10 w-48 h-48 rounded-full border-[28px] border-white/5"></div>
    <div class="absolute right-40 top-10 w-24 h-24 rounded-full border-[14px] border-brandgreen/15"></div>
    <p class="text-white/70 text-sm">Olá, Marina 👋</p>
    <h2 class="font-display font-bold text-2xl mt-1">Bem-vinda ao Portal de Chamados</h2>
    <p class="text-white/70 text-sm mt-1.5 max-w-lg">Acompanhe seus atendimentos e abra novas solicitações de suporte para as soluções Bondmann.</p>
    <div class="mt-5">{btn("Abrir novo chamado","green","plus")}</div>
  </div>
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-6">
    {kpi("Chamados abertos","3","+1","ticket","#2563EB",up=True)}
    {kpi("Em atendimento","2","2","clock","#6366F1",up=True)}
    {kpi("Aguardando você","1","1","bell","#F59E0B",up=False)}
    {kpi("Resolvidos no mês","14","+22%","check","#16A34A",up=True)}
  </div>
  <div class="flex items-center justify-between mb-3">
    <h3 class="font-display font-bold text-lg text-navy">Meus chamados</h3>
    <div class="flex gap-2">{btn("Filtrar","ghost","funnel")}{btn("Abrir chamado","primary","plus")}</div>
  </div>
  {table(["Código","Assunto","Status","Prioridade","SLA"], cli_rows, "cliente")}
'''
PAGES["cliente-dashboard.html"] = shell("cliente","cliente-dashboard.html","Painel",cli_content,"Visão geral dos seus atendimentos")

# CLIENTE — NOVO CHAMADO
def dropzone():
    files = [("manual_dosador.pdf","2,4 MB","doc"),("foto_vazamento.jpg","1,1 MB","doc")]
    chips = ""
    for n,s,_ in files:
        chips += f'''<div class="flex items-center gap-3 p-2.5 rounded-lg bg-surface ring-1 ring-line">
          <div class="w-9 h-9 rounded-lg bg-brandgreen-100 text-brandgreen-700 grid place-items-center">{icon("doc","w-5 h-5")}</div>
          <div class="min-w-0 flex-1"><div class="text-sm font-medium text-navy truncate">{n}</div><div class="text-xs text-muted">{s}</div></div>
          <button class="text-faint hover:text-sla_danger">✕</button></div>'''
    return f'''<div>
      <div class="border-2 border-dashed border-line rounded-xl p-8 text-center hover:border-brandgreen transition cursor-pointer bg-surface/40">
        <div class="w-12 h-12 mx-auto rounded-full bg-brandgreen-100 text-brandgreen-700 grid place-items-center mb-3">{icon("clip","w-6 h-6")}</div>
        <p class="text-sm font-semibold text-navy">Arraste arquivos ou <span class="text-brandgreen-700">selecione no computador</span></p>
        <p class="text-xs text-muted mt-1">PDF, JPG, PNG, MP4, DOCX, XLSX — até 10 MB por arquivo</p>
      </div>
      <div class="mt-3 space-y-2">{chips}</div></div>'''

novo_content = f'''
  <a href="cliente-dashboard.html" class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-navy mb-4"><svg class="w-4 h-4 rotate-180" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="{ICONS['chevright']}"/></svg> Voltar</a>
  <div class="grid lg:grid-cols-3 gap-6">
    <div class="lg:col-span-2 space-y-6">
      {card(f'<div class="p-6 space-y-5"><h3 class="font-display font-bold text-lg text-navy">Dados do chamado</h3>'
        + field("Departamento de destino", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Selecione o departamento…</option><option>TI</option><option>RH</option><option>Marketing</option></select>',"Só a equipe desse setor verá e atenderá o chamado.")
        + field("Categoria — opcional", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Selecione a categoria…</option><option>Suporte técnico</option><option>Solicitação de acesso</option><option>Dúvida</option><option>Outros</option></select>')
        + field("Assunto", input_ctl("Resuma o problema em uma frase"))
        + field("Prioridade", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Baixa</option><option selected>Média</option><option>Alta</option><option>Urgente</option></select>')
        + field("Descrição detalhada", '<textarea rows="5" placeholder="Descreva o que aconteceu, lote, linha de produção, etc." class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm placeholder:text-faint resize-none"></textarea>')
        + '</div>')}
      {card(f'<div class="p-6"><h3 class="font-display font-bold text-lg text-navy mb-4">Anexos</h3>{dropzone()}</div>')}
    </div>
    <div class="space-y-6">
      {card(f'''<div class="p-6">
        <h3 class="font-display font-bold text-lg text-navy mb-4">SLA previsto</h3>
        <div class="space-y-3">
          <div class="flex items-center justify-between p-3 rounded-lg bg-surface"><span class="text-sm text-muted">Departamento</span><span class="text-sm font-semibold text-navy">TI</span></div>
          <div class="flex items-center justify-between p-3 rounded-lg bg-surface"><span class="text-sm text-muted">1ª resposta em</span><span class="text-sm font-bold text-brandgreen-700">4 horas</span></div>
          <div class="flex items-center justify-between p-3 rounded-lg bg-surface"><span class="text-sm text-muted">Resolução em</span><span class="text-sm font-bold text-brandgreen-700">24 horas</span></div>
        </div>
        <div class="mt-4 rounded-lg p-3 text-xs flex gap-2" style="background:#EFF6FF;color:#2563EB">{icon("shield","w-4 h-4 shrink-0 mt-0.5","2")}<span>Prioridade <b>Urgente</b> reduz os prazos em 50% automaticamente.</span></div>
      </div>''')}
      {card(f'<div class="p-6 space-y-3">{btn("Abrir chamado","green","check","w-full")}{btn("Salvar rascunho","ghost",cls="w-full")}</div>')}
    </div>
  </div>
'''
PAGES["cliente-novo-chamado.html"] = shell("cliente","cliente-novo-chamado.html","Abrir chamado",novo_content,"Nova solicitação de suporte")

# CHAT thread (compartilhado)
def chat_thread(internal=False):
    def msg(side, ini, name, role, time, text, color="#5560B0", interna=False):
        mine = side=="right"
        bubble = ("bg-navy text-white" if mine else "bg-white ring-1 ring-line text-ink")
        if interna:
            bubble = "bg-[#FEF6E7] ring-1 ring-[#F0C674] text-[#7a5b12]"
        align = "flex-row-reverse" if mine else ""
        tag = '<span class="text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-[#F0934E]/20 text-[#b86a2a]">Nota interna</span>' if interna else ''
        return f'''<div class="flex gap-3 {align}">
          {avatar(ini,color,"w-8 h-8 text-[11px]")}
          <div class="max-w-[78%] {'items-end text-right' if mine else ''} flex flex-col">
            <div class="flex items-center gap-2 mb-1 {'flex-row-reverse' if mine else ''}"><span class="text-xs font-semibold text-navy">{name}</span><span class="text-[11px] text-faint">{role} · {time}</span>{tag}</div>
            <div class="px-4 py-2.5 rounded-2xl text-sm leading-relaxed {bubble} {'rounded-tr-sm' if mine else 'rounded-tl-sm'}">{text}</div>
          </div></div>'''
    msgs = [
      msg("left","MR","Marina Rocha","Cliente","14:02","Boa tarde! O dosador da linha 3 parou de calibrar o BD INDUSTRY corretamente após a última troca de lote. Segue foto em anexo.","#41B6E6"),
      msg("right","CF","Carlos Freitas","Operador","14:10","Olá, Marina! Obrigado pelo retorno. Já estou analisando. Pode me informar o número do lote impresso na bombona?","#5560B0"),
    ]
    if internal:
        msgs.append(msg("right","CF","Carlos Freitas","Operador","14:12","Verifiquei no histórico: cliente teve recalibração há 2 meses. Possível desgaste do bico. Acionar equipe de campo se persistir.","#5560B0",interna=True))
    msgs.append(msg("left","MR","Marina Rocha","Cliente","14:18","É o lote 8841. A foto mostra o display com erro E-12.","#41B6E6"))
    return '<div class="space-y-5">'+ "".join(msgs) +'</div>'

def chat_composer(internal=False):
    if internal:
        return f'''<div x-data="{{interna:false}}" class="border-t border-line p-4 transition-colors" :class="interna ? 'bg-[#FEF6E7]' : 'bg-white'">
      <div class="flex items-center justify-between mb-2">
        <label class="flex items-center gap-2 text-sm cursor-pointer select-none">
          <input type="checkbox" x-model="interna" class="sr-only peer">
          <span class="w-9 h-5 rounded-full bg-line peer-checked:bg-[#F0934E] relative transition after:absolute after:top-0.5 after:left-0.5 after:w-4 after:h-4 after:bg-white after:rounded-full after:transition peer-checked:after:translate-x-4"></span>
          <span class="font-medium" :class="interna ? 'text-[#b86a2a]' : 'text-muted'">Nota interna</span>
        </label>
        <span class="text-[11px]" :class="interna ? 'text-[#b86a2a]' : 'text-faint'" x-text="interna ? 'Visível apenas para operadores e admin' : 'Pressione Enter para enviar'"></span>
      </div>
      <div class="flex items-end gap-2">
        <button class="w-10 h-10 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy shrink-0">{icon("clip")}</button>
        <textarea rows="1" :placeholder="interna ? 'Escreva uma nota interna…' : 'Escreva sua mensagem…'" class="flex-1 px-3.5 py-2.5 rounded-lg ring-1 focus:ring-2 outline-none text-sm resize-none" :class="interna ? 'bg-white ring-[#F0C674] focus:ring-[#F0934E]' : 'bg-surface ring-line focus:ring-navy-500'"></textarea>
        <button class="w-10 h-10 grid place-items-center rounded-lg text-white shrink-0 transition" :class="interna ? 'bg-[#F0934E] hover:opacity-90' : 'bg-navy hover:bg-navy-700'">{icon("send")}</button>
      </div></div>'''
    return f'''<div class="border-t border-line p-4 bg-white">
      <div class="flex items-center justify-end mb-2"><span class="text-[11px] text-faint">Pressione Enter para enviar</span></div>
      <div class="flex items-end gap-2">
        <button class="w-10 h-10 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy shrink-0">{icon("clip")}</button>
        <textarea rows="1" placeholder="Escreva sua mensagem…" class="flex-1 px-3.5 py-2.5 rounded-lg bg-surface ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm resize-none"></textarea>
        <button class="w-10 h-10 grid place-items-center rounded-lg bg-navy text-white hover:bg-navy-700 shrink-0">{icon("send")}</button>
      </div></div>'''

def detail_sidebar(role):
    op = ('<div class="flex items-center justify-between"><span class="text-sm text-muted">Operador</span>'
          + ('<div class="flex items-center gap-2">'+avatar("CF","#5560B0","w-7 h-7 text-[11px]")+'<span class="text-sm font-semibold text-navy">Carlos Freitas</span></div>') + '</div>')
    timeline = ''
    if role!="cliente":
        events = [("Chamado criado","Marina Rocha · 27/06 14:02","#2563EB"),
                  ("Atribuído a Carlos Freitas","Sistema · 27/06 14:05","#6366F1"),
                  ("Status → Em atendimento","Carlos Freitas · 27/06 14:10","#6366F1"),
                  ("Prioridade → Alta","Carlos Freitas · 27/06 14:11","#F59E0B")]
        rows=""
        for i,(t,m,c) in enumerate(events):
            line = '' if i==len(events)-1 else '<span class="absolute left-[5px] top-4 bottom-[-14px] w-px bg-line"></span>'
            rows += f'<li class="relative pl-6 pb-3.5"><span class="absolute left-0 top-1 w-2.5 h-2.5 rounded-full ring-4 ring-white" style="background:{c}"></span>{line}<div class="text-sm font-medium text-navy leading-tight">{t}</div><div class="text-[11px] text-muted">{m}</div></li>'
        timeline = card(f'<div class="p-5"><h4 class="font-display font-bold text-navy mb-4">Histórico / Auditoria</h4><ul>{rows}</ul></div>')
    return f'''<div class="space-y-5">
      {card(f'''<div class="p-5 space-y-3.5">
        <h4 class="font-display font-bold text-navy mb-1">Detalhes</h4>
        <div class="flex items-center justify-between"><span class="text-sm text-muted">Status</span>{status_badge("EM_ATENDIMENTO")}</div>
        <div class="flex items-center justify-between"><span class="text-sm text-muted">Prioridade</span>{priority_badge("ALTA")}</div>
        <div class="flex items-center justify-between"><span class="text-sm text-muted">Categoria</span><span class="text-sm font-medium text-navy">Suporte técnico</span></div>
        <div class="flex items-center justify-between"><span class="text-sm text-muted">Empresa</span><span class="text-sm font-medium text-navy">Indústria Petroq. Sul</span></div>
        {op}
        <div class="flex items-center justify-between"><span class="text-sm text-muted">Aberto em</span><span class="text-sm font-medium text-navy">27/06/2026 14:02</span></div>
      </div>''')}
      {card(f'''<div class="p-5">
        <h4 class="font-display font-bold text-navy mb-3">SLA</h4>
        <div class="space-y-3">
          <div><div class="flex justify-between text-xs mb-1"><span class="text-muted">1ª resposta</span><span class="font-semibold text-sla_ok">Cumprido</span></div><div class="h-1.5 rounded-full bg-line overflow-hidden"><div class="h-full rounded-full bg-sla_ok" style="width:100%"></div></div></div>
          <div><div class="flex justify-between text-xs mb-1"><span class="text-muted">Resolução</span>{sla_chip("warn","2h 10m")}</div><div class="h-1.5 rounded-full bg-line overflow-hidden"><div class="h-full rounded-full bg-sla_warn" style="width:74%"></div></div></div>
        </div></div>''')}
      {card(f'''<div class="p-5"><h4 class="font-display font-bold text-navy mb-3">Anexos</h4>
        <div class="space-y-2">
          <a class="flex items-center gap-3 p-2 rounded-lg hover:bg-surface"><div class="w-8 h-8 rounded bg-brandgreen-100 text-brandgreen-700 grid place-items-center">{icon("doc","w-4 h-4")}</div><div class="text-sm text-navy">foto_vazamento.jpg</div></a>
          <a class="flex items-center gap-3 p-2 rounded-lg hover:bg-surface"><div class="w-8 h-8 rounded bg-brandgreen-100 text-brandgreen-700 grid place-items-center">{icon("doc","w-4 h-4")}</div><div class="text-sm text-navy">manual_dosador.pdf</div></a>
        </div>
        <p class="text-[11px] text-faint mt-2">Links assinados expiram em 1h (regerados a cada visualização).</p></div>''')}
      {timeline}
    </div>'''

def ticket_header(role):
    quick = ""
    if role!="cliente":
        quick = f'''<div class="flex flex-wrap gap-2">
          <select class="px-3 py-2 rounded-lg bg-white ring-1 ring-line text-sm font-medium text-navy outline-none focus:ring-2 focus:ring-navy-500"><option>Em atendimento</option><option>Novo</option><option>Aguardando cliente</option><option>Resolvido</option></select>
          <select class="px-3 py-2 rounded-lg bg-white ring-1 ring-line text-sm font-medium text-navy outline-none focus:ring-2 focus:ring-navy-500"><option>Alta</option><option>Baixa</option><option>Média</option><option>Urgente</option></select>
          {btn("Resolver","green","check")}</div>'''
    else:
        quick = btn("Reabrir / responder","primary","send")
    return f'''<div class="flex flex-wrap items-start justify-between gap-4 mb-5">
      <div>
        <div class="flex items-center gap-3 mb-1">
          <span class="font-mono text-[13px] font-bold text-brandgreen-700">BOND-2026-00412</span>
          {status_badge("EM_ATENDIMENTO")}{priority_badge("ALTA")}
        </div>
        <h2 class="font-display font-bold text-xl text-navy">Falha no dosador da linha 3</h2>
      </div>
      {quick}
    </div>'''

def detail_screen(role, active, title):
    internal = role!="cliente"
    content = f'''
      <a href="{'operador-fila-lista.html' if role!='cliente' else 'cliente-dashboard.html'}" class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-navy mb-4"><svg class="w-4 h-4 rotate-180" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="{ICONS['chevright']}"/></svg> Voltar</a>
      {ticket_header(role)}
      <div class="grid lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2">
          {card(f'<div class="flex flex-col h-[620px]"><div class="flex-1 overflow-y-auto p-5">{chat_thread(internal)}</div>{chat_composer(internal)}</div>',"overflow-hidden")}
        </div>
        <div>{detail_sidebar(role)}</div>
      </div>'''
    return shell(role, active, title, content, "BOND-2026-00412 · Indústria Petroquímica Sul")

PAGES["cliente-chamado-detalhe.html"] = detail_screen("cliente","cliente-dashboard.html","Detalhe do chamado")
PAGES["operador-atendimento.html"] = detail_screen("operador","operador-atendimento.html","Atendimento")

# ============================================================================
#  WORKSPACE DO OPERADOR
# ============================================================================
op_rows = "".join(ticket_row(t,"operador") for t in TICKETS)
filters = f'''<div class="flex flex-wrap items-center gap-2.5 mb-4">
  <div class="relative flex-1 min-w-[220px]"><span class="absolute left-3 top-1/2 -translate-y-1/2 text-faint">{icon("search")}</span>
    <input placeholder="Buscar por código, assunto ou empresa…" class="w-full pl-10 pr-3 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm placeholder:text-faint"></div>
  <select class="px-3 py-2.5 rounded-lg bg-white ring-1 ring-line text-sm text-navy outline-none focus:ring-2 focus:ring-navy-500"><option>Todos os status</option><option>Novo</option><option>Em atendimento</option><option>Aguardando</option><option>Resolvido</option></select>
  <select class="px-3 py-2.5 rounded-lg bg-white ring-1 ring-line text-sm text-navy outline-none focus:ring-2 focus:ring-navy-500"><option>Toda prioridade</option><option>Urgente</option><option>Alta</option><option>Média</option><option>Baixa</option></select>
  <select class="px-3 py-2.5 rounded-lg bg-white ring-1 ring-line text-sm text-navy outline-none focus:ring-2 focus:ring-navy-500"><option>Todos operadores</option><option>Carlos Freitas</option><option>Marcos Silva</option><option>Sem atribuição</option></select>
  {btn("SLA vencendo","subtle","clock")}
</div>'''
bulk = f'''<div class="flex items-center justify-between mb-3">
  <div class="flex items-center gap-2 text-sm text-muted"><span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-navy-100 text-navy font-medium">2 selecionados</span>
    {btn("Atribuir a…","ghost","user","!py-1.5 !px-3 !text-[13px]")}{btn("Mudar status","ghost",None,"!py-1.5 !px-3 !text-[13px]")}</div>
  <div class="flex items-center gap-2"><span class="text-sm text-muted">Visão:</span>
    <a href="operador-fila-lista.html" class="px-3 py-1.5 rounded-lg text-sm font-semibold bg-navy text-white">Lista</a>
    <a href="operador-fila-kanban.html" class="px-3 py-1.5 rounded-lg text-sm font-semibold bg-white ring-1 ring-line text-navy hover:bg-surface">Kanban</a></div>
</div>'''
op_list_content = f'''
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-6">
    {kpi("Fila total","37","+5","inbox","#2563EB",up=True)}
    {kpi("SLA em risco","6","6","clock","#F59E0B",up=False)}
    {kpi("Vencidos","2","2","bell","#DC2626",up=False)}
    {kpi("Resolvidos hoje","19","+8","check","#16A34A",up=True)}
  </div>
  {filters}{bulk}
  {table(["Código","Assunto","Empresa","Status","Prioridade","SLA","Operador"], op_rows, "operador")}
'''
PAGES["operador-fila-lista.html"] = shell("operador","operador-fila-lista.html","Fila de chamados",op_list_content,"Todos os chamados · multi-empresa")

# KANBAN
def kanban_card(t):
    code,title,emp,st,pr,sla,slatxt,op = t
    stripe = {"ok":"#16A34A","warn":"#F59E0B","danger":"#DC2626"}[sla]
    opv = avatar(op,"#5560B0","w-6 h-6 text-[10px]") if op!="—" else '<span class="text-[11px] text-faint">sem operador</span>'
    return f'''<a href="operador-atendimento.html" class="block bg-white rounded-xl shadow-soft border border-line/70 p-3.5 hover:shadow-card transition cursor-grab active:cursor-grabbing">
      <div class="flex items-center justify-between mb-2"><span class="font-mono text-[11px] font-semibold text-brandgreen-700">{code}</span>{priority_badge(pr)}</div>
      <p class="text-sm font-medium text-navy leading-snug mb-2">{title}</p>
      <p class="text-xs text-muted mb-3">{emp}</p>
      <div class="flex items-center justify-between border-t border-line pt-2.5">{sla_chip(sla,slatxt)}{opv}</div>
    </a>'''

def kanban_col(label, color, tickets, count):
    cards = "".join(kanban_card(t) for t in tickets)
    add = '<button class="w-full mt-1 py-2 rounded-lg border border-dashed border-line text-xs font-medium text-faint hover:text-navy hover:border-navy/30">+ adicionar</button>'
    return f'''<div class="w-[300px] shrink-0 flex flex-col">
      <div class="flex items-center gap-2 mb-3 px-1"><span class="w-2.5 h-2.5 rounded-full" style="background:{color}"></span>
        <h3 class="font-display font-bold text-sm text-navy">{label}</h3>
        <span class="text-xs font-semibold text-muted bg-surface2 rounded-full px-2 py-0.5">{count}</span></div>
      <div class="flex-1 space-y-3 p-2 rounded-xl bg-surface/60 min-h-[200px]">{cards}{add}</div>
    </div>'''

T = TICKETS
kanban = f'''
  {bulk.replace('operador-fila-kanban.html" class="px-3 py-1.5 rounded-lg text-sm font-semibold bg-white ring-1 ring-line text-navy hover:bg-surface','operador-fila-kanban.html" class="px-3 py-1.5 rounded-lg text-sm font-semibold bg-navy text-white').replace('operador-fila-lista.html" class="px-3 py-1.5 rounded-lg text-sm font-semibold bg-navy text-white','operador-fila-lista.html" class="px-3 py-1.5 rounded-lg text-sm font-semibold bg-white ring-1 ring-line text-navy hover:bg-surface')}
  <div class="flex gap-5 overflow-x-auto pb-4">
    {kanban_col("Novo","#2563EB",[T[2],T[5]],"8")}
    {kanban_col("Em atendimento","#6366F1",[T[0],T[3]],"14")}
    {kanban_col("Aguardando cliente","#F59E0B",[T[1]],"9")}
    {kanban_col("Resolvido","#16A34A",[T[4]],"6")}
  </div>
'''
PAGES["operador-fila-kanban.html"] = shell("operador","operador-fila-kanban.html","Kanban de chamados",kanban,"Arraste os cards entre as colunas (Sortable.js + HTMX)")

# ============================================================================
#  PAINEL ADMIN
# ============================================================================
CHARTJS = '<script src="./assets/chart.umd.js"></script>'

admin_dash_scripts = '''<script>
window.addEventListener('DOMContentLoaded',()=>{
  const navy='#2E466F', green='#7FA53D', grid='#E2E8F0', muted='#64748B';
  Chart.defaults.font.family='Inter'; Chart.defaults.color=muted; Chart.defaults.font.size=11;
  new Chart(document.getElementById('cVol'),{type:'line',data:{labels:['01','05','09','13','17','21','25','27'],
    datasets:[{label:'Abertos',data:[12,18,15,22,19,26,21,24],borderColor:navy,backgroundColor:'rgba(46,70,111,.08)',fill:true,tension:.4,borderWidth:2,pointRadius:0},
    {label:'Resolvidos',data:[8,14,16,18,20,22,23,25],borderColor:green,backgroundColor:'rgba(127,165,61,.08)',fill:true,tension:.4,borderWidth:2,pointRadius:0}]},
    options:{plugins:{legend:{position:'bottom',labels:{usePointStyle:true,boxWidth:6,padding:16}}},scales:{x:{grid:{display:false}},y:{grid:{color:grid},border:{display:false}}},maintainAspectRatio:false}});
  new Chart(document.getElementById('cStatus'),{type:'doughnut',data:{labels:['Novo','Em atendimento','Aguardando','Resolvido'],
    datasets:[{data:[8,14,9,42],backgroundColor:['#2563EB','#6366F1','#F59E0B','#16A34A'],borderWidth:0}]},
    options:{cutout:'68%',plugins:{legend:{position:'bottom',labels:{usePointStyle:true,boxWidth:6,padding:12}}},maintainAspectRatio:false}});
  new Chart(document.getElementById('cOp'),{type:'bar',data:{labels:['C. Freitas','M. Silva','J. Lima','P. Nunes','R. Alves'],
    datasets:[{label:'Resolvidos',data:[42,38,29,24,18],backgroundColor:navy,borderRadius:6,barThickness:22}]},
    options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{grid:{color:grid},border:{display:false}},y:{grid:{display:false}}},maintainAspectRatio:false}});
});
</script>'''

admin_recent = "".join(ticket_row(t,"operador") for t in TICKETS[:4])
admin_dash = f'''
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-6">
    {kpi("Conformidade SLA","94,2%","+3,1%","shield","#16A34A",up=True)}
    {kpi("Tempo médio atend.","3h 12m","-18m","clock","#2E466F",up=True)}
    {kpi("Chamados no mês","312","+12%","ticket","#5560B0",up=True)}
    {kpi("CSAT","4,6 / 5","+0,2","check","#7FA53D",up=True)}
  </div>
  <div class="grid lg:grid-cols-3 gap-6 mb-6">
    {card(f'<div class="p-5"><div class="flex items-center justify-between mb-4"><h3 class="font-display font-bold text-navy">Volume de chamados</h3><span class="text-xs text-muted">Junho/2026</span></div><div class="h-[260px]"><canvas id="cVol"></canvas></div></div>',"lg:col-span-2")}
    {card(f'<div class="p-5"><h3 class="font-display font-bold text-navy mb-4">Por status</h3><div class="h-[260px]"><canvas id="cStatus"></canvas></div></div>')}
  </div>
  <div class="grid lg:grid-cols-3 gap-6">
    {card(f'<div class="p-5"><h3 class="font-display font-bold text-navy mb-4">Produtividade por operador</h3><div class="h-[240px]"><canvas id="cOp"></canvas></div></div>')}
    {card(f'<div class="p-5"><div class="flex items-center justify-between mb-2"><h3 class="font-display font-bold text-navy">Chamados recentes</h3><a href="operador-fila-lista.html" class="text-sm font-semibold text-brandgreen-700 hover:underline">Ver todos</a></div></div>'
      + f'<div class="overflow-x-auto"><table class="w-full"><tbody>{admin_recent}</tbody></table></div>',"lg:col-span-2 overflow-hidden")}
  </div>
'''
PAGES["admin-dashboard.html"] = shell("admin","admin-dashboard.html","Dashboard",admin_dash,"Indicadores e KPIs do service desk",
   actions=btn("Exportar CSV","ghost","download"), extra=CHARTJS+admin_dash_scripts)

# EMPRESAS
def emp_row(nome, cnpj, plano, ativos, status):
    pcol = {"Ouro":"#F0934E","Prata":"#94A3B8","Bronze":"#b87333"}[plano]
    on = status=="Ativa"
    return f'''<tr class="border-t border-line hover:bg-surface/60">
      <td class="px-4 py-3.5"><div class="flex items-center gap-3">{avatar(nome[:2].upper(),"#2E466F","w-9 h-9 text-xs")}<div><div class="text-sm font-semibold text-navy">{nome}</div><div class="text-xs text-muted font-mono">{cnpj}</div></div></div></td>
      <td class="px-4 py-3.5"><span class="inline-flex items-center gap-1.5 text-sm font-semibold" style="color:{pcol}"><span class="w-2 h-2 rounded-full" style="background:{pcol}"></span>{plano}</span></td>
      <td class="px-4 py-3.5 text-sm text-muted">{ativos} usuários</td>
      <td class="px-4 py-3.5"><span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold" style="background:{'#16A34A' if on else '#94A3B8'}1a;color:{'#16A34A' if on else '#64748B'}">{status}</span></td>
      <td class="px-4 py-3.5 text-right pr-5"><div class="inline-flex gap-1"><button class="w-8 h-8 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy">{icon("edit2","w-4 h-4")}</button><button class="w-8 h-8 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy">{icon("dots","w-4 h-4")}</button></div></td></tr>'''
emp_rows = "".join([
  emp_row("TecnoMetal Usinagem","12.345.678/0001-90","Ouro",8,"Ativa"),
  emp_row("Indústria Petroquímica Sul","98.765.432/0001-10","Ouro",23,"Ativa"),
  emp_row("Agro Vale Verde","45.678.912/0001-33","Prata",6,"Ativa"),
  emp_row("Frigorífico Boi Dourado","78.912.345/0001-55","Bronze",4,"Ativa"),
  emp_row("Metalúrgica Horizonte","32.165.498/0001-72","Prata",11,"Inativa"),
])
emp_modal_body = (
    field("Nome fantasia", input_ctl("Ex.: TecnoMetal Usinagem","building"))
    + '<div class="grid sm:grid-cols-2 gap-4">'
    + field("CNPJ", input_ctl("00.000.000/0001-00"))
    + field("Plano de SLA", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Bronze</option><option>Prata</option><option selected>Ouro</option></select>')
    + '</div>'
    + field("Status", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Ativa</option><option>Inativa</option></select>')
)
emp_modal_footer = btn("Cancelar","ghost",attrs='@click="open=false"') + btn("Salvar empresa","primary","check",attrs='@click="open=false"')
admin_emp = f'''<div x-data="{{ open:false }}">
  <div class="flex items-center justify-between mb-4">
    <div class="relative w-80"><span class="absolute left-3 top-1/2 -translate-y-1/2 text-faint">{icon("search")}</span><input placeholder="Buscar empresa…" class="w-full pl-10 pr-3 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"></div>
    {btn("Nova empresa","primary","plus",attrs='@click="open=true"')}
  </div>
  {table(["Empresa","Plano de SLA","Usuários","Status"], emp_rows, "admin").replace('<th class="pl-4 py-3 w-10"><input type="checkbox" class="rounded border-line text-navy focus:ring-navy-500"></th>','')}
  {modal("open","Nova empresa", emp_modal_body, emp_modal_footer)}
</div>'''
PAGES["admin-empresas.html"] = shell("admin","admin-empresas.html","Empresas",admin_emp,"Gestão de empresas (tenants)")

# PLANOS SLA
def plano_card(nome, cor, preco, resp, resol, featured=False):
    ring = "ring-2 ring-brandgreen" if featured else "ring-1 ring-line/60"
    badge = '<span class="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full text-[11px] font-bold text-white bg-brandgreen-600">MAIS USADO</span>' if featured else ''
    def rowline(p, r1, r2, c):
        return f'<div class="flex items-center justify-between py-2 border-b border-line/70 last:border-0"><span class="inline-flex items-center gap-1.5 text-sm text-navy">{priority_badge(p)}</span><span class="text-sm text-muted">{r1} / <b class="text-navy">{r2}</b></span></div>'
    return f'''<div class="relative bg-white rounded-xl shadow-card {ring} p-6">{badge}
      <div class="flex items-center gap-2 mb-1"><span class="w-3 h-3 rounded-full" style="background:{cor}"></span><h3 class="font-display font-bold text-lg text-navy">{nome}</h3></div>
      <p class="text-xs text-muted mb-4">Resposta / Resolução por prioridade</p>
      <div class="mb-4">{rowline("BAIXA","16h","48h",cor)}{rowline("MEDIA","8h","24h",cor)}{rowline("ALTA","4h","12h",cor)}{rowline("URGENTE","2h","6h",cor)}</div>
      <div class="rounded-lg bg-surface p-3 text-xs text-muted flex gap-2">{icon("shield","w-4 h-4 shrink-0 mt-0.5","2")}<span>URGENTE = 50% do tempo de ALTA (derivado automaticamente).</span></div>
      <div class="mt-4 flex gap-2">{btn("Editar","ghost","edit2","flex-1")}<button class="w-10 grid place-items-center rounded-lg ring-1 ring-line text-muted hover:text-navy">{icon("dots","w-4 h-4")}</button></div>
    </div>'''
admin_planos = f'''
  <div class="flex items-center justify-between mb-5">
    <div class="rounded-lg bg-feedback-info-bg p-3 text-xs flex gap-2 max-w-xl" style="background:#EFF6FF;color:#2563EB">{icon("clock","w-4 h-4 shrink-0 mt-0.5","2")}<span>Alterações nos planos valem para <b>novos chamados</b>. Fallback global: resposta 12h / resolução 24h quando o plano não está configurado.</span></div>
    {btn("Novo plano","primary","plus")}
  </div>
  <div class="grid md:grid-cols-3 gap-6">
    {plano_card("Bronze","#b87333","",None,None)}
    {plano_card("Prata","#94A3B8","",None,None)}
    {plano_card("Ouro","#F0934E","",None,None,featured=True)}
  </div>
'''
PAGES["admin-planos-sla.html"] = shell("admin","admin-planos-sla.html","Planos de SLA",admin_planos,"Configuração de tempos de atendimento")

# CATEGORIAS
def cat_row(nome, desc, qtd, ativo=True):
    return f'''<tr class="border-t border-line hover:bg-surface/60">
      <td class="px-4 py-3.5"><div class="flex items-center gap-3"><div class="w-9 h-9 rounded-lg bg-brandgreen-100 text-brandgreen-700 grid place-items-center">{icon("tag","w-4 h-4")}</div><div class="text-sm font-semibold text-navy">{nome}</div></div></td>
      <td class="px-4 py-3.5 text-sm text-muted">{desc}</td>
      <td class="px-4 py-3.5 text-sm text-muted">{qtd} chamados</td>
      <td class="px-4 py-3.5"><span class="w-9 h-5 rounded-full relative inline-block {'bg-brandgreen-600' if ativo else 'bg-line'}"><span class="absolute top-0.5 {'right-0.5' if ativo else 'left-0.5'} w-4 h-4 bg-white rounded-full shadow"></span></span></td>
      <td class="px-4 py-3.5 text-right pr-5"><button class="w-8 h-8 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy">{icon("edit2","w-4 h-4")}</button></td></tr>'''
cat_rows = "".join([
  cat_row("Suporte técnico de produto","Dúvidas de aplicação, diluição e desempenho",128),
  cat_row("Solicitação de documento","FISPQ, laudos, certificados de análise",64),
  cat_row("Logística / Entrega","Prazos, avarias e rastreamento",41),
  cat_row("Comercial / Plano","Mudança de plano, propostas, cobrança",27),
  cat_row("Reclamação de qualidade","Não conformidade de lote",19,ativo=False),
])
admin_cat = f'''
  <div class="flex items-center justify-between mb-4">
    <p class="text-sm text-muted">Catálogo global de categorias · ativar/desativar sem excluir o histórico.</p>
    {btn("Nova categoria","primary","plus")}
  </div>
  {table(["Categoria","Descrição","Uso","Ativa"], cat_rows, "admin").replace('<th class="pl-4 py-3 w-10"><input type="checkbox" class="rounded border-line text-navy focus:ring-navy-500"></th>','')}
'''
PAGES["admin-categorias.html"] = shell("admin","admin-categorias.html","Categorias",admin_cat,"Catálogo de categorias de chamado")

# USUARIOS
ROLECHIP = {"ADMIN":("#2E466F","Administrador"),"OPERADOR":("#5560B0","Operador"),"CLIENTE":("#41B6E6","Cliente")}
def user_row(ini, nome, email, role, emp, ativo=True):
    c,l = ROLECHIP[role]
    return f'''<tr class="border-t border-line hover:bg-surface/60">
      <td class="px-4 py-3.5"><div class="flex items-center gap-3">{avatar(ini,c,"w-9 h-9 text-xs")}<div><div class="text-sm font-semibold text-navy">{nome}</div><div class="text-xs text-muted">{email}</div></div></div></td>
      <td class="px-4 py-3.5"><span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold" style="background:{c}1a;color:{c}">{l}</span></td>
      <td class="px-4 py-3.5 text-sm text-muted">{emp}</td>
      <td class="px-4 py-3.5"><span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold" style="background:{'#16A34A' if ativo else '#94A3B8'}1a;color:{'#16A34A' if ativo else '#64748B'}">{'Ativo' if ativo else 'Inativo'}</span></td>
      <td class="px-4 py-3.5 text-right pr-5"><button class="w-8 h-8 grid place-items-center rounded-lg text-muted hover:bg-surface hover:text-navy">{icon("dots","w-4 h-4")}</button></td></tr>'''
user_rows = "".join([
  user_row("AB","Ana Bondmann","ana@bondmann.com.br","ADMIN","Bondmann (interno)"),
  user_row("CF","Carlos Freitas","carlos@bondmann.com.br","OPERADOR","Bondmann (interno)"),
  user_row("MS","Marcos Silva","marcos@bondmann.com.br","OPERADOR","Bondmann (interno)"),
  user_row("MR","Marina Rocha","marina@petrosul.com.br","CLIENTE","Indústria Petroquímica Sul"),
  user_row("JT","João Tavares","joao@tecnometal.com.br","CLIENTE","TecnoMetal Usinagem",ativo=False),
])
user_modal_body = (
    '<div class="rounded-lg p-3 text-xs flex gap-2 mb-1" style="background:#EFF6FF;color:#2563EB">' + icon("mail","w-4 h-4 shrink-0 mt-0.5","2") + '<span>O usuário receberá um e-mail com link para definir a senha e acessar o portal.</span></div>'
    + field("E-mail", input_ctl("pessoa@empresa.com.br","mail",type="email"))
    + '<div class="grid sm:grid-cols-2 gap-4">'
    + field("Papel", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Cliente</option><option>Operador</option><option>Administrador</option></select>')
    + field("Empresa", '<select class="w-full px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"><option>Bondmann (interno)</option><option>TecnoMetal Usinagem</option><option>Indústria Petroquímica Sul</option></select>')
    + '</div>'
)
user_modal_footer = btn("Cancelar","ghost",attrs='@click="open=false"') + btn("Enviar convite","green","send",attrs='@click="open=false"')
admin_user = f'''<div x-data="{{ open:false }}">
  <div class="flex items-center justify-between mb-4">
    <div class="flex gap-2">
      <div class="relative w-72"><span class="absolute left-3 top-1/2 -translate-y-1/2 text-faint">{icon("search")}</span><input placeholder="Buscar usuário…" class="w-full pl-10 pr-3 py-2.5 rounded-lg bg-white ring-1 ring-line focus:ring-2 focus:ring-navy-500 outline-none text-sm"></div>
      <select class="px-3 py-2.5 rounded-lg bg-white ring-1 ring-line text-sm text-navy outline-none focus:ring-2 focus:ring-navy-500"><option>Todos os papéis</option><option>Administrador</option><option>Operador</option><option>Cliente</option></select>
    </div>
    {btn("Convidar usuário","primary","plus",attrs='@click="open=true"')}
  </div>
  {table(["Usuário","Papel","Empresa","Status"], user_rows, "admin").replace('<th class="pl-4 py-3 w-10"><input type="checkbox" class="rounded border-line text-navy focus:ring-navy-500"></th>','')}
  {modal("open","Convidar usuário", user_modal_body, user_modal_footer)}
</div>'''
PAGES["admin-usuarios.html"] = shell("admin","admin-usuarios.html","Usuários",admin_user,"Gestão de usuários e papéis (ADMIN / OPERADOR / CLIENTE)")

# RELATORIOS
rel_rows = "".join(ticket_row(t,"operador") for t in TICKETS)
admin_rel = f'''
  {card(f'''<div class="p-5 flex flex-wrap items-end gap-4">
    <div>{field("De", input_ctl(value="01/06/2026",ic="calendar"))}</div>
    <div>{field("Até", input_ctl(value="30/06/2026",ic="calendar"))}</div>
    <div>{field("Empresa", '<select class="w-56 px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line outline-none text-sm focus:ring-2 focus:ring-navy-500"><option>Todas as empresas</option><option>TecnoMetal Usinagem</option><option>Indústria Petroquímica Sul</option></select>')}</div>
    <div>{field("Status", '<select class="w-44 px-3.5 py-2.5 rounded-lg bg-white ring-1 ring-line outline-none text-sm focus:ring-2 focus:ring-navy-500"><option>Todos</option><option>Resolvido</option><option>Em atendimento</option></select>')}</div>
    <div class="flex-1"></div>
    <div class="flex gap-2">{btn("Aplicar","primary","funnel")}{btn("Exportar CSV","green","download")}</div>
  </div>''')}
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-5 my-6">
    {kpi("Total no período","312","+12%","ticket","#2E466F",up=True)}
    {kpi("Dentro do SLA","294","94,2%","shield","#16A34A",up=True)}
    {kpi("Fora do SLA","18","5,8%","clock","#DC2626",up=False)}
    {kpi("TMA","3h 12m","-18m","trend","#5560B0",up=True)}
  </div>
  <div class="flex items-center justify-between mb-3"><h3 class="font-display font-bold text-lg text-navy">Resultado</h3><span class="text-sm text-muted">312 registros</span></div>
  {table(["Código","Assunto","Empresa","Status","Prioridade","SLA","Operador"], rel_rows, "operador")}
'''
PAGES["admin-relatorios.html"] = shell("admin","admin-relatorios.html","Relatórios",admin_rel,"KPIs, conformidade de SLA e exportação CSV",
   actions=btn("Exportar CSV","green","download"))

# ============================================================================
#  PERFIL & CONFIGURAÇÕES
# ============================================================================
def pref_toggle(label, desc, on=True):
    return f'''<div class="flex items-start justify-between gap-4 py-3 border-b border-line/70 last:border-0">
      <div><div class="text-sm font-medium text-navy">{label}</div><div class="text-xs text-muted">{desc}</div></div>
      <span class="w-9 h-5 rounded-full relative inline-block shrink-0 mt-0.5 {'bg-brandgreen-600' if on else 'bg-line'}"><span class="absolute top-0.5 {'right-0.5' if on else 'left-0.5'} w-4 h-4 bg-white rounded-full shadow"></span></span>
    </div>'''
perfil_content = f'''
  {card(f'''<div class="p-6 flex flex-wrap items-center gap-5">
    {avatar("AB","#7FA53D","w-16 h-16 text-xl")}
    <div class="flex-1 min-w-0"><h2 class="font-display font-bold text-xl text-navy">Ana Bondmann</h2><p class="text-sm text-muted">ana@bondmann.com.br</p></div>
    <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold" style="background:#2E466F1a;color:#2E466F"><span class="w-2 h-2 rounded-full" style="background:#2E466F"></span>Administrador</span>
  </div>''')}
  <div class="grid lg:grid-cols-3 gap-6 mt-6">
    <div class="lg:col-span-2 space-y-6">
      {card(f'<div class="p-6 space-y-5"><h3 class="font-display font-bold text-lg text-navy">Dados pessoais</h3>'
        + '<div class="grid sm:grid-cols-2 gap-4">' + field("Nome completo", input_ctl(value="Ana Bondmann",ic="user")) + field("E-mail", input_ctl(value="ana@bondmann.com.br",ic="mail",type="email")) + '</div>'
        + '<div class="grid sm:grid-cols-2 gap-4">' + field("Telefone", input_ctl(value="(51) 99999-0000")) + field("Cargo", input_ctl(value="Gerente de Suporte")) + '</div>'
        + '<div class="flex justify-end gap-2 pt-1">' + btn("Cancelar","ghost") + btn("Salvar alterações","primary","check") + '</div></div>')}
      {card(f'<div class="p-6 space-y-5"><h3 class="font-display font-bold text-lg text-navy">Segurança</h3>'
        + field("Senha atual", input_ctl("••••••••",ic="lock",type="password"))
        + '<div class="grid sm:grid-cols-2 gap-4">' + field("Nova senha", input_ctl("Mínimo 8 caracteres",ic="lock",type="password")) + field("Confirmar nova senha", input_ctl("Repita a nova senha",ic="lock",type="password")) + '</div>'
        + '<div class="flex justify-end pt-1">' + btn("Atualizar senha","primary","shield") + '</div></div>')}
    </div>
    <div class="space-y-6">
      {card(f'<div class="p-6"><h3 class="font-display font-bold text-lg text-navy mb-3">Notificações</h3>'
        + pref_toggle("Novo chamado atribuído","E-mail quando um chamado for atribuído a você",True)
        + pref_toggle("SLA em risco","Alerta quando faltar menos de 25% do prazo",True)
        + pref_toggle("SLA vencido","Alerta imediato de chamado vencido",True)
        + pref_toggle("Resumo diário","E-mail com indicadores do dia",False)
        + '</div>')}
      {card(f'<div class="p-6"><h3 class="font-display font-bold text-lg text-navy mb-1">Sessão</h3><p class="text-xs text-muted mb-4">Encerrar acesso neste dispositivo.</p>' + btn("Sair da conta","danger","logout","w-full") + '</div>')}
    </div>
  </div>
'''
PAGES["perfil.html"] = shell("admin","perfil.html","Perfil & Configurações",perfil_content,"Gerencie seus dados, segurança e notificações")

# ============================================================================
#  PÁGINAS DE ERRO
# ============================================================================
def error_page(title, code, headline, message, primary_label, primary_href, secondary_label, secondary_href):
    big_mark = MARK.format(cls="w-40 h-40 text-white/10")
    return head(title) + f'''
    <div class="min-h-screen bg-navy-900 relative overflow-hidden flex items-center justify-center p-6">
      <div class="absolute -left-24 -top-24 w-[420px] h-[420px] rounded-full border-[60px] border-white/5"></div>
      <div class="absolute right-[-120px] bottom-[-120px] w-[380px] h-[380px] rounded-full border-[48px] border-white/5"></div>
      <div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">{big_mark}</div>
      <div class="relative text-center max-w-md">
        <div class="flex justify-center mb-8">{logo("dark","text-2xl")}</div>
        <div class="font-display font-black text-white text-[96px] leading-none">{code}</div>
        <h1 class="font-display font-bold text-2xl text-white mt-2">{headline}</h1>
        <p class="text-white/70 mt-3 leading-relaxed">{message}</p>
        <div class="mt-8 flex flex-wrap items-center justify-center gap-3">
          <a href="{primary_href}"><span class="inline-flex items-center gap-2 rounded-lg text-sm font-semibold px-5 py-2.5 bg-brandgreen-600 text-white hover:bg-brandgreen-700">{icon("home","w-4 h-4","2")}{primary_label}</span></a>
          <a href="{secondary_href}" class="text-sm font-semibold text-white/80 hover:text-white">{secondary_label}</a>
        </div>
      </div>
    </div>''' + FOOT
PAGES["erro-403.html"] = error_page("Acesso negado","403","Acesso negado",
   "Você não tem permissão para acessar esta área. Em um sistema multi-tenant, cada perfil (Cliente, Operador, Admin) enxerga apenas o que lhe compete.",
   "Voltar ao início","index.html","Falar com o administrador","#")
PAGES["erro-404.html"] = error_page("Página não encontrada","404","Página não encontrada",
   "O endereço que você tentou acessar não existe ou foi movido. Verifique o link ou volte para a tela inicial do portal.",
   "Voltar ao início","index.html","Ir para o login","login.html")

# ============================================================================
#  DESIGN SYSTEM
# ============================================================================
def swatch(name, hexv, dark=False):
    tc = "text-white" if dark else "text-navy"
    return f'''<div class="rounded-xl overflow-hidden ring-1 ring-line/60 shadow-soft">
      <div class="h-20" style="background:{hexv}"></div>
      <div class="p-3 bg-white"><div class="text-[13px] font-semibold text-navy">{name}</div><div class="text-xs text-muted font-mono">{hexv}</div></div></div>'''

ds_colors_brand = "".join([swatch("navy-900","#0E2747"),swatch("navy-700","#1C3A63"),swatch("navy / 2955C","#2E466F"),swatch("navy-500","#3D5A8A"),swatch("green / 367C","#ACC76B"),swatch("green-600","#7FA53D")])
ds_colors_sub = "".join([swatch("BD Clean","#1FB98C"),swatch("BD Auto","#41B6E6"),swatch("BD Industry","#5560B0"),swatch("BD Fluid","#E63E62"),swatch("BD Max","#F0934E"),swatch("BD Service","#1B8A8F")])
ds_colors_sem = "".join([swatch("Novo","#2563EB"),swatch("Em atend.","#6366F1"),swatch("Aguardando","#F59E0B"),swatch("Resolvido","#16A34A"),swatch("SLA risco","#F59E0B"),swatch("SLA vencido","#DC2626")])

ds_body = f'''
<div class="min-h-screen">
  <div class="bg-navy-900 px-10 py-12">
    <div class="max-w-[1200px] mx-auto">
      {logo("dark","text-3xl")}
      <h1 class="font-display font-bold text-white text-4xl mt-8">Design System</h1>
      <p class="text-white/70 mt-2 max-w-2xl">Fundamentos visuais do Portal de Chamados, derivados do Manual de Identidade Visual da Bondmann Química (PANTONE 2955C / 367C) e do Plano Mestre de Desenvolvimento.</p>
    </div>
  </div>
  <div class="max-w-[1200px] mx-auto px-10 py-12 space-y-14">

    <section>
      <h2 class="font-display font-bold text-2xl text-navy mb-1">Logotipo</h2>
      <p class="text-muted text-sm mb-5">Marca molecular (7 anéis) + wordmark. Versões para fundo escuro e claro.</p>
      <div class="grid sm:grid-cols-2 gap-5">
        <div class="rounded-xl p-8 bg-navy-900 flex items-center justify-center">{logo("dark","text-2xl")}</div>
        <div class="rounded-xl p-8 bg-white ring-1 ring-line flex items-center justify-center">{logo("light","text-2xl")}</div>
      </div>
    </section>

    <section>
      <h2 class="font-display font-bold text-2xl text-navy mb-1">Cores da marca</h2>
      <p class="text-muted text-sm mb-5">Azul institucional e verde — cores oficiais.</p>
      <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">{ds_colors_brand}</div>
      <h3 class="font-display font-bold text-lg text-navy mt-8 mb-3">Sub-marcas</h3>
      <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">{ds_colors_sub}</div>
      <h3 class="font-display font-bold text-lg text-navy mt-8 mb-3">Cores semânticas — status &amp; SLA</h3>
      <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">{ds_colors_sem}</div>
    </section>

    <section>
      <h2 class="font-display font-bold text-2xl text-navy mb-1">Tipografia</h2>
      <p class="text-muted text-sm mb-5">Manual: <i>Info Book Italic</i> (títulos) + <i>Info Text</i> (texto). Mapeadas para a web como <b>Archivo</b> (display) + <b>Inter</b> (interface).</p>
      {card(f'''<div class="p-7 space-y-4">
        <div class="font-display font-black text-5xl text-navy">Bondmann Química</div>
        <div class="font-display font-bold text-3xl text-navy">Archivo Bold · Títulos</div>
        <div class="text-xl font-semibold text-navy">Inter Semibold · Subtítulos</div>
        <div class="text-base text-ink">Inter Regular · Corpo de texto. O respeito às determinações do manual é imprescindível à identidade visual.</div>
        <div class="text-sm text-muted">Inter · Texto secundário e legendas</div>
      </div>''')}
    </section>

    <section>
      <h2 class="font-display font-bold text-2xl text-navy mb-1">Componentes</h2>
      <p class="text-muted text-sm mb-5">Blocos reutilizáveis da interface.</p>
      <div class="grid lg:grid-cols-2 gap-5">
        {card(f'<div class="p-6"><div class="text-xs font-bold uppercase tracking-wider text-muted mb-4">Botões</div><div class="flex flex-wrap gap-3">{btn("Primário","primary","check")}{btn("Verde","green","plus")}{btn("Contorno","ghost","funnel")}{btn("Sutil","subtle")}{btn("Perigo","danger")}</div></div>')}
        {card(f'<div class="p-6"><div class="text-xs font-bold uppercase tracking-wider text-muted mb-4">Status do chamado</div><div class="flex flex-wrap gap-2">{status_badge("NOVO")}{status_badge("EM_ATENDIMENTO")}{status_badge("AGUARDANDO")}{status_badge("RESOLVIDO")}</div><div class="text-xs font-bold uppercase tracking-wider text-muted mt-5 mb-3">Prioridade</div><div class="flex flex-wrap gap-2">{priority_badge("BAIXA")}{priority_badge("MEDIA")}{priority_badge("ALTA")}{priority_badge("URGENTE")}</div></div>')}
        {card(f'<div class="p-6"><div class="text-xs font-bold uppercase tracking-wider text-muted mb-4">Indicadores de SLA</div><div class="flex flex-wrap gap-2 items-center">{sla_chip("ok","No prazo")}{sla_chip("warn","2h 10m restantes")}{sla_chip("danger","Vencido há 35m")}</div><p class="text-xs text-muted mt-3">Verde no prazo · amarelo &lt;25% · vermelho piscante quando vencido.</p></div>')}
        {card(f'<div class="p-6"><div class="text-xs font-bold uppercase tracking-wider text-muted mb-4">Campos de formulário</div><div class="space-y-3">{field("E-mail", input_ctl("voce@empresa.com.br","mail"))}{input_ctl("Buscar…","search")}</div></div>')}
      </div>
    </section>
  </div>
</div>'''
PAGES["design-system.html"] = head("Design System") + ds_body + FOOT

# ============================================================================
#  ÍNDICE / GALERIA
# ============================================================================
GROUPS = [
  ("Fundamentos","#2E466F",[("design-system.html","Design System","Cores, tipografia, logo e componentes")]),
  ("Autenticação","#41B6E6",[("login.html","Login","Acesso ao portal"),("recuperar-senha.html","Recuperar senha","Reset por e-mail (link 1h)")]),
  ("Portal do Cliente","#1FB98C",[("cliente-dashboard.html","Dashboard","Visão geral + meus chamados"),("cliente-novo-chamado.html","Abrir chamado","Formulário + upload + SLA previsto"),("cliente-chamado-detalhe.html","Detalhe + Chat","Conversa e anexos")]),
  ("Workspace do Operador","#5560B0",[("operador-fila-lista.html","Fila — Lista","Filtros, SLA visual, ações em lote"),("operador-fila-kanban.html","Fila — Kanban","Colunas por status (drag & drop)"),("operador-atendimento.html","Atendimento","Chat + nota interna + auditoria")]),
  ("Painel Admin","#F0934E",[("admin-dashboard.html","Dashboard / KPIs","Gráficos Chart.js"),("admin-categorias.html","Categorias","Catálogo global"),("admin-usuarios.html","Usuários","Papéis + departamento (TI/RH/Marketing)"),("admin-relatorios.html","Relatórios","Filtros + export CSV")]),
  ("Conta & Sistema","#1B8A8F",[("perfil.html","Perfil & Configurações","Dados, segurança e notificações"),("erro-403.html","Acesso negado (403)","Sem permissão / RBAC"),("erro-404.html","Não encontrado (404)","Página inexistente")]),
]
def gallery_card(href, title, desc, color):
    return f'''<a href="{href}" class="group block bg-white rounded-xl shadow-card border border-line/60 overflow-hidden hover:-translate-y-0.5 hover:shadow-lg transition">
      <div class="h-2" style="background:{color}"></div>
      <div class="p-5">
        <div class="flex items-center justify-between">
          <h3 class="font-display font-bold text-navy group-hover:text-brandgreen-700 transition">{title}</h3>
          <span class="text-faint group-hover:text-brandgreen-700 transition">{icon("chevright","w-4 h-4","2.5")}</span>
        </div>
        <p class="text-sm text-muted mt-1">{desc}</p>
      </div></a>'''
sections = ""
total = 0
for name, color, items in GROUPS:
    total += len(items)
    cards = "".join(gallery_card(h,t,d,color) for h,t,d in items)
    sections += f'''<section class="mb-10">
      <div class="flex items-center gap-2.5 mb-4"><span class="w-3 h-3 rounded-full" style="background:{color}"></span><h2 class="font-display font-bold text-xl text-navy">{name}</h2><span class="text-sm text-muted">({len(items)})</span></div>
      <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">{cards}</div></section>'''

index_body = f'''
<div class="bg-navy-900 relative overflow-hidden">
  <div class="absolute -left-20 -top-20 w-96 h-96 rounded-full border-[56px] border-white/5"></div>
  <div class="absolute right-10 top-10 w-40 h-40 rounded-full border-[22px] border-brandgreen/10"></div>
  <div class="max-w-[1180px] mx-auto px-10 py-14 relative">
    {logo("dark","text-2xl")}
    <h1 class="font-display font-bold text-white text-4xl mt-8">Portal de Chamados — Protótipo de alta fidelidade</h1>
    <p class="text-white/70 mt-3 max-w-2xl leading-relaxed">{total} telas construídas a partir do Manual de Identidade Visual da Bondmann Química e do Plano Mestre de Desenvolvimento. Stack: Tailwind CSS · HTMX · Alpine.js · Chart.js — a mesma definida na Seção 0.4 do plano.</p>
    <div class="mt-6 flex flex-wrap gap-2.5">
      <span class="px-3 py-1 rounded-full text-xs font-semibold bg-white/10 text-white">Uso interno</span>
      <span class="px-3 py-1 rounded-full text-xs font-semibold bg-white/10 text-white">Roteamento por departamento (TI · RH · Marketing)</span>
      <span class="px-3 py-1 rounded-full text-xs font-semibold bg-white/10 text-white">SLA visual</span>
      <span class="px-3 py-1 rounded-full text-xs font-semibold bg-brandgreen/20 text-brandgreen">Identidade Bondmann</span>
    </div>
  </div>
  <div class="h-1.5 w-full bg-gradient-to-r from-brandgreen via-brandgreen-600 to-navy-500"></div>
</div>
<div class="max-w-[1180px] mx-auto px-10 py-12">{sections}
  <p class="text-center text-xs text-faint mt-6">© 2026 Bondmann Química · Protótipo gerado a partir do design system · bondmann.com.br</p>
</div>'''
PAGES["index.html"] = head("Telas do Portal") + index_body + FOOT

# ============================================================================
#  ESCRITA DOS ARQUIVOS
# ============================================================================
# Sistema interno: sem venda de planos, sem cadastro de empresas nem signup público.
for _obsoleta in ("cadastro.html", "admin-empresas.html", "admin-planos-sla.html"):
    PAGES.pop(_obsoleta, None)
    _p = os.path.join(OUT, _obsoleta)
    if os.path.exists(_p):
        os.remove(_p)

written = []
for fname, html in PAGES.items():
    with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
        f.write(html)
    written.append(fname)
print(f"OK — {len(written)} arquivos gerados:")
for w in sorted(written):
    print("  •", w)




