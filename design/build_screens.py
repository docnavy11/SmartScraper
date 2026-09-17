# -*- coding: utf-8 -*-
import json, os, html

BG='#0B0E14'; SURF='#10141D'; SURF2='#151A25'; SURF3='#1C2331'
LINE='#212836'; LINE2='#2E3849'
TXT='#E3E8F0'; TXT2='#99A3B5'; TXT3='#7F8AA1'
ACC='#35C2C2'; ACCD='#0E3F40'
OK='#3FB950'; WARN='#D29922'; FAIL='#F85149'; AGENT='#A371F7'

SANS="'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif"
MONO="'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"

W, H = 1440, 900

ICONS = {
 'play':'M6 4.5l12 7.5-12 7.5z',
 'pause':'M9 5v14M15 5v14',
 'stop':'M6 6h12v12H6z',
 'check':'M4.5 12.5l5 5 10-11',
 'x':'M6 6l12 12M18 6L6 18',
 'alert':'M12 4.5L2.5 20h19zM12 10v4.5M12 17.5v.5',
 'clock':'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5.2l3.5 2.2',
 'refresh':'M20 12a8 8 0 11-2.4-5.7M20 3v4.5h-4.5',
 'code':'M9 7l-5 5 5 5M15 7l5 5-5 5',
 'download':'M12 3v12M7 11l5 5 5-5M4 20h16',
 'gear':'M12 9a3 3 0 100 6 3 3 0 000-6zM12 2.5l1.6 2.6 3-.4 .5 3 2.7 1.4-1.4 2.7 1.4 2.7-2.7 1.4-.5 3-3-.4L12 21.5l-1.6-2.6-3 .4-.5-3-2.7-1.4L5.6 12 4.2 9.3l2.7-1.4 .5-3 3 .4z',
 'plus':'M12 5v14M5 12h14',
 'chev':'M9 5l7 7-7 7',
 'ext':'M14 4h6v6M20 4l-9 9M18 13v6a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h6',
 'filter':'M3 5h18l-7 8v6l-4 2v-8z',
 'search':'M11 4a7 7 0 100 14 7 7 0 000-14zM16 16l4.5 4.5',
 'term':'M4 5h16v14H4zM8 10l2.5 2.5L8 15M13 15h4',
 'db':'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
 'globe':'M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c2.5 2.5 3.8 5.6 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.6-3.8-9S9.5 5.5 12 3z',
 'key':'M15.5 3a5.5 5.5 0 00-5.2 7.3L3 17.6V21h3.4l1.3-1.3v-2h2v-2h2l1.4-1.4A5.5 5.5 0 1015.5 3zM17 7.5h.01',
 'shield':'M12 3l8 3v5.5c0 4.8-3.3 8.4-8 9.5-4.7-1.1-8-4.7-8-9.5V6z',
 'zap':'M13 3L5 13.5h6L11 21l8-10.5h-6z',
 'branch':'M7 4v10M7 18.5v1M17 4v4a4 4 0 01-4 4H7M7 14a2.2 2.2 0 100 4.5A2.2 2.2 0 007 14zM7 1.8A2.2 2.2 0 107 6.3 2.2 2.2 0 007 1.8zM17 1.8a2.2 2.2 0 100 4.5 2.2 2.2 0 000-4.5z',
 'file':'M14 3H6a1 1 0 00-1 1v16a1 1 0 001 1h12a1 1 0 001-1V8zM14 3v5h5M8 13h8M8 17h5',
 'trash':'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
 'image':'M4 5h16v14H4zM4 16l4.5-4.5 3.5 3.5 3-3L20 16M9 9.5h.01',
 'user':'M12 12a4 4 0 100-8 4 4 0 000 8zM4.5 20.5c1-3.8 4-5.5 7.5-5.5s6.5 1.7 7.5 5.5',
 'send':'M21 3L10.5 13.5M21 3l-6.8 18-3.7-7.5L3 10z',
 'eye':'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 9.2a2.8 2.8 0 100 5.6 2.8 2.8 0 000-5.6z',
 'link':'M10.5 13.5a4 4 0 005.7 0l3-3a4 4 0 10-5.7-5.7l-1.6 1.6M13.5 10.5a4 4 0 00-5.7 0l-3 3a4 4 0 105.7 5.7l1.6-1.6',
 'calendar':'M5 6h14v14H5zM5 10h14M9 3v4M15 3v4',
 'sun':'M12 7.5a4.5 4.5 0 100 9 4.5 4.5 0 000-9zM12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8',
 'sliders':'M4 7h10M18 7h2M4 17h4M12 17h8M16 4v6M8 14v6',
 'cpu':'M7 7h10v10H7zM9.5 3v4M14.5 3v4M9.5 17v4M14.5 17v4M3 9.5h4M3 14.5h4M17 9.5h4M17 14.5h4',
 'copy':'M9 9h11v11H9zM5 15H4V4h11v1',
}

def icon(n, s=14, c=None, sw=1.75):
    c = c or 'currentColor'
    return ('<svg width="%d" height="%d" viewBox="0 0 24 24" fill="none" stroke="%s" '
            'stroke-width="%s" stroke-linecap="round" stroke-linejoin="round" '
            'style="flex-shrink:0;display:block;"><path d="%s"></path></svg>') % (s, s, c, sw, ICONS[n])

def esc(t): return html.escape(str(t), quote=False)

# ---------- status ----------
ST = {
 'ok':      ('●', OK,    'passed'),
 'drift':   ('▲', WARN,  'drift'),
 'fail':    ('✕', FAIL,  'failed'),
 'run':     ('◐', ACC,   'running'),
 'queued':  ('○', TXT3,  'queued'),
 'paused':  ('‖', TXT3,  'paused'),
 'agent':   ('◆', AGENT, 'agent'),
}

def dot(kind, size=11):
    g, c, _ = ST[kind]
    return '<span style="color:%s;font-size:%dpx;line-height:1;font-family:%s;">%s</span>' % (c, size, MONO, g)

def status(kind, label=None, size=11):
    g, c, lab = ST[kind]
    label = label or lab
    return ('<span style="display:inline-flex;align-items:center;gap:6px;color:%s;'
            'font-size:%dpx;font-family:%s;white-space:nowrap;">'
            '<span style="font-size:%dpx;line-height:1;">%s</span>%s</span>') % (c, size, MONO, size, g, esc(label))

def chip(text, c=TXT2, bg=None, mono=True, pad='2px 6px'):
    bg = bg or SURF2
    return ('<span style="display:inline-flex;align-items:center;padding:%s;border-radius:3px;'
            'background:%s;color:%s;font-size:11px;font-family:%s;white-space:nowrap;letter-spacing:.01em;">%s</span>'
            ) % (pad, bg, c, MONO if mono else SANS, esc(text))

def btn(label, kind='ghost', ic=None, href=None, small=False):
    pads = '0 10px' if small else '0 12px'
    hgt  = 26 if small else 30
    if kind == 'primary':
        st = 'background:%s;color:%s;border:1px solid %s;font-weight:600;' % (ACC, BG, ACC)
    elif kind == 'danger':
        st = 'background:transparent;color:%s;border:1px solid %s;' % (FAIL, '#4A2224')
    elif kind == 'solid':
        st = 'background:%s;color:%s;border:1px solid %s;' % (SURF3, TXT, LINE2)
    else:
        st = 'background:transparent;color:%s;border:1px solid %s;' % (TXT2, LINE2)
    inner = ''
    if ic: inner += icon(ic, 13)
    if label: inner += '<span>%s</span>' % esc(label)
    base = ('display:inline-flex;align-items:center;gap:6px;height:%dpx;padding:%s;border-radius:4px;'
            'font-family:%s;font-size:12px;cursor:pointer;text-decoration:none;box-sizing:border-box;%s'
            ) % (hgt, pads, SANS, st)
    if href:
        return '<a href="%s" style="%s">%s</a>' % (href, base, inner)
    return '<button type="button" style="%s">%s</button>' % (base, inner)

def iconbtn(ic, label, href=None, c=TXT2):
    base = ('display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;'
            'border-radius:4px;border:1px solid %s;background:transparent;color:%s;cursor:pointer;') % (LINE2, c)
    if href:
        return '<a href="%s" aria-label="%s" style="%s;text-decoration:none;">%s</a>' % (href, esc(label), base, icon(ic, 14))
    return '<button type="button" aria-label="%s" style="%s">%s</button>' % (esc(label), base, icon(ic, 14))

def label(t, c=None):
    return ('<div style="font-size:10.5px;font-family:%s;color:%s;letter-spacing:.09em;'
            'text-transform:uppercase;font-weight:600;">%s</div>') % (SANS, c or TXT3, esc(t))

def mono(t, size=12, c=None, weight=400):
    return '<span style="font-family:%s;font-size:%dpx;color:%s;font-weight:%d;font-variant-numeric:tabular-nums;">%s</span>' % (
        MONO, size, c or TXT, weight, esc(t))

def panel(inner, pad=0, grow=False, style=''):
    g = 'flex:1 1 0;min-height:0;' if grow else ''
    return ('<div style="background:%s;border:1px solid %s;border-radius:4px;overflow:hidden;'
            'display:flex;flex-direction:column;%s%s%s">%s</div>') % (
        SURF, LINE, ('padding:%dpx;' % pad) if pad else '', g, style, inner)

def phead(title, right='', sub=''):
    r = '<div style="display:flex;align-items:center;gap:8px;">%s</div>' % right if right else ''
    s = '<span style="font-size:11px;font-family:%s;color:%s;">%s</span>' % (MONO, TXT3, esc(sub)) if sub else ''
    return ('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:10px;'
            'border-bottom:1px solid %s;background:%s;">'
            '<span style="font-size:11.5px;font-family:%s;font-weight:600;color:%s;letter-spacing:.03em;">%s</span>'
            '%s<span style="flex-grow:1;"></span>%s</div>') % (LINE, SURF2, SANS, TXT2, esc(title), s, r)

# ---------- table ----------
def table(cols, rows, zebra=False):
    # cols: list of (label, width_css, align)
    th = ''
    for (lab, wd, al) in cols:
        th += ('<th style="text-align:%s;padding:0 10px;height:28px;font-family:%s;font-size:10px;'
               'letter-spacing:.09em;text-transform:uppercase;color:%s;font-weight:600;'
               'border-bottom:1px solid %s;background:%s;white-space:nowrap;%s">%s</th>') % (
            al, SANS, TXT3, LINE, SURF2, ('width:%s;' % wd) if wd else '', esc(lab))
    trs = ''
    for i, r in enumerate(rows):
        cells = ''
        bg = SURF3 if (zebra and i % 2 == 1) else 'transparent'
        for j, c in enumerate(r):
            al = cols[j][2]
            cells += ('<td style="text-align:%s;padding:0 10px;height:30px;border-bottom:1px solid %s;'
                      'font-family:%s;font-size:12px;color:%s;white-space:nowrap;'
                      'font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;">%s</td>') % (
                al, LINE, MONO, TXT, c)
        trs += '<tr style="background:%s;">%s</tr>' % (bg, cells)
    return ('<table style="width:100%%;border-collapse:collapse;table-layout:fixed;">'
            '<thead><tr>%s</tr></thead><tbody>%s</tbody></table>') % (th, trs)

def alink(text, href, c=None, size=12, mono_f=True, weight=500):
    return ('<a href="%s" style="color:%s;font-family:%s;font-size:%dpx;font-weight:%d;'
            'text-decoration:none;">%s</a>') % (href, c or TXT, MONO if mono_f else SANS, size, weight, esc(text))

# ---------- shell ----------
NAV = [('Overview','Main.dc.html'), ('Scrapers','Scrapers.dc.html'), ('Runs','Runs.dc.html'),
       ('Records','Records.dc.html'), ('Repairs','Repairs.dc.html'), ('Network','Network.dc.html'),
       ('Delivery','Delivery.dc.html'), ('Settings','Settings.dc.html')]

def topbar(active):
    items = ''
    for name, href in NAV:
        on = (name == active)
        col = TXT if on else TXT2
        bg  = SURF3 if on else 'transparent'
        bdg = ''
        if name == 'Repairs':
            bdg = ('<span style="display:inline-flex;align-items:center;justify-content:center;min-width:15px;'
                   'height:15px;padding:0 4px;border-radius:7px;background:%s;color:%s;font-size:10px;'
                   'font-family:%s;font-weight:600;">2</span>') % (WARN, BG, MONO)
        items += ('<a href="%s" style="display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 9px;'
                  'border-radius:4px;background:%s;color:%s;font-family:%s;font-size:12.5px;font-weight:%d;'
                  'text-decoration:none;">%s%s</a>') % (href, bg, col, SANS, 600 if on else 400, esc(name), bdg)
    logo = ('<a href="Main.dc.html" style="display:inline-flex;align-items:center;gap:7px;text-decoration:none;margin-right:6px;">'
            '<span style="display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;'
            'border:1.5px solid %s;border-radius:4px;color:%s;">%s</span>'
            '<span style="font-family:%s;font-size:13px;font-weight:600;color:%s;letter-spacing:-.01em;">smartscraper</span>'
            '</a>') % (ACC, ACC, icon('term', 11, ACC, 2), SANS, TXT)
    right = ('<div style="display:flex;align-items:center;gap:10px;">'
             '<span style="display:inline-flex;align-items:center;gap:5px;font-family:%s;font-size:11.5px;color:%s;">'
             '<span style="color:%s;">◐</span>2 running</span>'
             '<span style="width:1px;height:16px;background:%s;"></span>'
             '<span style="font-family:%s;font-size:11.5px;color:%s;font-variant-numeric:tabular-nums;">$18.42 <span style="color:%s;">mtd</span></span>'
             '<span style="width:1px;height:16px;background:%s;"></span>%s</div>') % (
        MONO, TXT2, ACC, LINE2, MONO, TXT2, TXT3, LINE2, iconbtn('sun', 'Switch to light theme'))
    return ('<header style="height:48px;flex-shrink:0;display:flex;align-items:center;gap:2px;padding:0 14px;'
            'background:%s;border-bottom:1px solid %s;">%s<nav style="display:flex;align-items:center;gap:2px;">%s</nav>'
            '<span style="flex-grow:1;"></span>%s</header>') % (SURF, LINE, logo, items, right)

def pagehead(title, sub='', actions='', tabs=None):
    subhtml = ''
    if sub:
        subhtml = '<span style="font-family:%s;font-size:12px;color:%s;">%s</span>' % (MONO, TXT3, sub)
    tabhtml = ''
    if tabs:
        ts = ''
        for name, on in tabs:
            ts += ('<span style="display:inline-flex;align-items:center;height:30px;padding:0 2px;margin-right:16px;'
                   'font-family:%s;font-size:12.5px;color:%s;font-weight:%d;border-bottom:2px solid %s;">%s</span>'
                   ) % (SANS, TXT if on else TXT2, 600 if on else 400, ACC if on else 'transparent', esc(name))
        tabhtml = ('<div style="height:32px;padding:0 16px;display:flex;align-items:flex-end;gap:0;'
                   'border-bottom:1px solid %s;background:%s;">%s</div>') % (LINE, BG, ts)
    return ('<div style="flex-shrink:0;background:%s;">'
            '<div style="height:52px;padding:0 16px;display:flex;align-items:center;gap:12px;%s">'
            '<h1 style="margin:0;font-family:%s;font-size:17px;font-weight:600;color:%s;letter-spacing:-.01em;">%s</h1>'
            '%s<span style="flex-grow:1;"></span>'
            '<div style="display:flex;align-items:center;gap:8px;">%s</div></div>%s</div>') % (
        BG, '' if tabs else ('border-bottom:1px solid %s;' % LINE), SANS, TXT, esc(title), subhtml, actions, tabhtml)

def board(fname, title, active_nav, body, pagehead_html, interactive=True):
    doc = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&amp;family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap">
<style>
body { margin: 0; background: %(BG)s; font-family: %(SANS)s; -webkit-font-smoothing: antialiased; }
a { color: %(ACC)s; }
a:hover { color: #7BE0DF; }
button { font: inherit; }
table { border-spacing: 0; }
</style>
</helmet>
<div style="width:%(W)dpx;height:%(H)dpx;box-sizing:border-box;background:%(BG)s;color:%(TXT)s;font-family:%(SANS)s;display:flex;flex-direction:column;overflow:hidden;">
%(TOP)s
%(HEAD)s
<main style="flex:1 1 0;min-height:0;display:flex;flex-direction:column;overflow:hidden;">
%(BODY)s
</main>
</div>
</x-dc>
<script data-dc-script data-props='{"$preview":{"width":%(W)d,"height":%(H)d}}'>
class Component extends DCLogic {
  renderVals() { return {}; }
}
</script>
</body>
</html>
""" % dict(BG=BG, SANS=SANS, ACC=ACC, TXT=TXT, W=W, H=H,
           TOP=topbar(active_nav), HEAD=pagehead_html, BODY=body)
    return doc

BOARDS = {}
def add(fname, title, nav, pagehead_html, body):
    BOARDS[fname] = (title, board(fname, title, nav, body, pagehead_html))

PAD = 'padding:16px;display:flex;flex-direction:column;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;'

def tile(lab, val, sub='', c=None, ic=None):
    return ('<div style="flex:1 1 0;background:%s;border:1px solid %s;border-radius:4px;padding:11px 13px;'
            'display:flex;flex-direction:column;gap:5px;box-sizing:border-box;">'
            '<div style="display:flex;align-items:center;gap:6px;color:%s;">%s%s</div>'
            '<div style="font-family:%s;font-size:23px;font-weight:600;color:%s;line-height:1;'
            'font-variant-numeric:tabular-nums;letter-spacing:-.02em;">%s</div>'
            '<div style="font-family:%s;font-size:11px;color:%s;">%s</div></div>') % (
        SURF, LINE, TXT3, icon(ic, 12, TXT3) if ic else '',
        '<span style="font-size:10.5px;font-family:%s;letter-spacing:.09em;text-transform:uppercase;font-weight:600;">%s</span>' % (SANS, esc(lab)),
        MONO, c or TXT, esc(val), MONO, TXT3, esc(sub))


def cbox(checked=False):
    return ('<span style="display:inline-flex;align-items:center;justify-content:center;width:13px;height:13px;'
            'border:1px solid %s;border-radius:3px;background:%s;color:%s;font-family:%s;font-size:9px;">%s</span>'
            ) % (ACC if checked else LINE2, ACC if checked else 'transparent', BG, MONO, '\u2713' if checked else '')

def smallbtn(t, c=None, href=None):
    st = ('display:inline-flex;align-items:center;height:20px;padding:0 7px;border:1px solid %s;'
          'border-radius:3px;font-family:%s;font-size:10px;color:%s;cursor:pointer;white-space:nowrap;'
          'text-decoration:none;') % (LINE2, MONO, c or TXT2)
    if href:
        return '<a href="%s" style="%s">%s</a>' % (href, st, esc(t))
    return '<button type="button" style="%s">%s</button>' % (st, esc(t))

def banner(kind, text, actions=''):
    g, c, _ = ST[kind]
    bgc = {'fail': '#26161A', 'drift': '#241D10', 'ok': '#10201A', 'agent': '#1C1630'}.get(kind, SURF2)
    return ('<div style="display:flex;align-items:center;gap:10px;padding:9px 12px;background:%s;'
            'border:1px solid %s;border-radius:4px;flex-shrink:0;">'
            '<span style="color:%s;font-family:%s;font-size:11px;">%s</span>'
            '<span style="font-family:%s;font-size:11.5px;color:%s;flex-grow:1;line-height:1.45;">%s</span>'
            '<div style="display:flex;gap:6px;flex-shrink:0;">%s</div></div>') % (
        bgc, c, c, MONO, g, MONO, TXT2, esc(text), actions)

def grid_cell(pct, n, tot):
    if n == 0:
        return ('<div style="flex:1 1 0;height:26px;border:1px dashed %s;border-radius:3px;display:flex;'
                'align-items:center;justify-content:center;font-family:%s;font-size:9.5px;color:%s;">\u2014</div>'
                ) % (LINE, MONO, TXT3)
    c = OK if pct >= 85 else (WARN if pct >= 50 else FAIL)
    bgc = {OK: '#10251A', WARN: '#26200F', FAIL: '#2A1517'}[c]
    return ('<div style="flex:1 1 0;height:26px;background:%s;border:1px solid %s;border-radius:3px;display:flex;'
            'flex-direction:column;align-items:center;justify-content:center;gap:0;">'
            '<span style="font-family:%s;font-size:10.5px;color:%s;font-weight:600;line-height:1.1;">%d%%</span>'
            '<span style="font-family:%s;font-size:8.5px;color:%s;line-height:1.1;">%d</span></div>') % (
        bgc, c, MONO, c, pct, MONO, TXT3, tot)

# ============================== 1. OVERVIEW ==============================
_bars = ''
import random
random.seed(7)
_pat = [(6,0,0),(5,0,0),(7,0,0),(6,1,0),(5,0,0),(8,0,0),(6,0,1),(7,1,0),(9,0,0),(6,0,0),(7,0,0),(8,1,0),
        (6,0,0),(7,0,0),(5,0,1),(8,0,0),(6,2,0),(7,0,0),(9,0,0),(6,0,0),(5,0,0),(7,1,0),(6,0,0),(4,0,0)]
for i,(g,w,f) in enumerate(_pat):
    total = g+w+f
    segs = ''
    for n,c in ((f,FAIL),(w,WARN),(g,OK)):
        if n: segs += '<div style="height:%dpx;background:%s;"></div>' % (max(3, n*4), c)
    _bars += ('<div style="flex:1 1 0;display:flex;flex-direction:column;justify-content:flex-end;gap:1px;" '
              'title="%02d:00 — %d runs"></div>' % (i, total)).replace('></div>', '>%s</div>' % segs)

_strip = ('<div style="display:flex;align-items:flex-end;gap:2px;height:44px;">%s</div>'
          '<div style="display:flex;justify-content:space-between;font-family:%s;font-size:10px;color:%s;margin-top:5px;">'
          '<span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>now</span></div>') % (_bars, MONO, TXT3)

_attention = ''
for name, msg, kind, href, acked in [
    ('shop-eu-prices', 'null rate on price 0.66, expected \u2264 0.05', 'drift', 'RunDetail.dc.html', ''),
    ('news-archive',   'blocked at escalation 4 of 5 \u00b7 cloudflare managed challenge', 'fail', 'RunDetail.dc.html', ''),
    ('news-paywall',   'login profile expired 2 days ago \u00b7 every run will fail until you sign in', 'fail', 'Network.dc.html', ''),
    ('competitor-skus','row count 640, band expects 900\u20131400', 'drift', 'RunDetail.dc.html', 'you \u00b7 2h ago'),
    ('job-board-eu',   'login profile jobs-sso expires in 3 days', 'drift', 'Network.dc.html', 'muted until fri')]:
    g, c, _ = ST[kind]
    if acked:
        right = ('<span style="font-family:%s;font-size:10px;color:%s;">%s</span>%s') % (
            MONO, TXT3, esc(acked), smallbtn('Reopen'))
    else:
        right = smallbtn('Ack') + smallbtn('Mute 24h')
    _attention += ('<div style="display:flex;align-items:center;gap:9px;padding:7px 12px;'
                   'border-bottom:1px solid %s;opacity:%s;">'
                   '<span style="color:%s;font-family:%s;font-size:11px;">%s</span>'
                   '<a href="%s" style="font-family:%s;font-size:12px;color:%s;font-weight:500;width:124px;'
                   'flex-shrink:0;text-decoration:none;">%s</a>'
                   '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;overflow:hidden;'
                   'text-overflow:ellipsis;white-space:nowrap;">%s</span>'
                   '<div style="display:flex;align-items:center;gap:5px;flex-shrink:0;">%s</div>'
                   '<a href="%s" aria-label="Open %s" style="text-decoration:none;">%s</a></div>') % (
        LINE, '0.5' if acked else '1', c, MONO, g, href, MONO, TXT, esc(name), MONO, TXT2, esc(msg),
        right, href, esc(name), icon('chev', 12, TXT3))

_activity = ''
for t, ic, c, txt in [
    ('12:06:14','run',  ACC,   'vendor-stock  run #4188 started · patchright'),
    ('12:05:02','agent',AGENT, 'repair agent proposed shop-eu-prices v8'),
    ('12:04:41','fail', FAIL,  'shop-eu-prices  run #4182 validation failed'),
    ('12:04:09','ok',   OK,    'hn-frontpage  run #4187 passed · 30 rows'),
    ('12:02:55','ok',   OK,    'example-products  run #4186 passed · 412 rows'),
    ('12:01:30','agent',AGENT, 'fallback extraction returned 118 rows · sonnet-5'),
    ('11:58:12','ok',   OK,    'gov-tenders  delivered 88 rows → webhook'),
    ('11:55:40','run',  ACC,   'competitor-skus  escalated to patchright+proxy'),
    ('11:52:03','fail', FAIL,  'news-archive  run #4180 blocked · cf challenge'),
    ('11:47:19','ok',   OK,    'example-products  run #4179 passed · 409 rows'),
    ('11:45:00','ok',   OK,    'vendor-stock  delivered 1,203 rows → s3'),
    ('11:41:22','ok',   OK,    'hn-frontpage  run #4177 passed · 30 rows'),
    ('11:38:44','agent',AGENT, 'builder finished gov-tenders v1 · 14 turns'),
    ('11:36:10','ok',   OK,    'competitor-skus  run #4175 passed · 640 rows'),
    ('11:31:52','run',  ACC,   'example-products  run #4174 started · patchright'),
    ('11:30:07','ok',   OK,    'gov-tenders  run #4173 passed · 88 rows'),
    ('11:27:41','fail', FAIL,  'shop-eu-prices  proxy pool eu-resi timed out'),
    ('11:26:18','ok',   OK,    'vendor-stock  run #4171 passed · 1,189 rows'),
    ('11:22:55','ok',   OK,    'hn-frontpage  run #4170 passed · 30 rows'),
    ('11:19:30','queued',TXT3, 'news-archive  queued behind 2 runs'),
    ('11:16:02','ok',   OK,    'example-products  run #4168 passed · 414 rows'),
    ('11:12:38','agent',AGENT, 'audit sampled vendor-stock · no drift found')]:
    g = ST[ic][0]
    _activity += ('<div style="display:flex;align-items:center;gap:9px;padding:6px 12px;border-bottom:1px solid %s;">'
                  '<span style="font-family:%s;font-size:11px;color:%s;font-variant-numeric:tabular-nums;">%s</span>'
                  '<span style="color:%s;font-family:%s;font-size:10px;">%s</span>'
                  '<span style="font-family:%s;font-size:11.5px;color:%s;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">%s</span>'
                  '</div>') % (LINE, MONO, TXT3, t, c, MONO, g, MONO, TXT2, esc(txt))

_sched = ''
for tm, name, eng in [('12:15','example-products','patchright'), ('12:15','hn-frontpage','http'),
                      ('13:00','shop-eu-prices','patchright'), ('13:00','vendor-stock','patchright'),
                      ('18:00','competitor-skus','http'), ('06:00','news-archive','camoufox'),
                      ('07:00','gov-tenders','http')]:
    _sched += ('<div style="display:flex;align-items:center;gap:8px;padding:6px 12px;border-bottom:1px solid %s;">'
               '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:500;width:36px;font-variant-numeric:tabular-nums;">%s</span>'
               '<span style="font-family:%s;font-size:11.5px;color:%s;flex-grow:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">%s</span>'
               '%s</div>') % (LINE, MONO, ACC, tm, MONO, TXT2, esc(name), chip(eng, TXT3))

ov_body = ('<div style="%s">'
  '<div style="display:flex;gap:12px;flex-shrink:0;">%s%s%s%s%s</div>'
  '<div style="display:flex;gap:12px;flex:1 1 0;min-height:0;">'
    '<div style="flex:1.45 1 0;display:flex;flex-direction:column;gap:12px;min-width:0;">'
      '%s'
      '%s'
    '</div>'
    '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
    '<div style="width:250px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '</div></div>') % (
  PAD,
  tile('scrapers', '8', '6 healthy · 1 drift · 1 failed', ic='db'),
  tile('runs today', '148', '141 passed · 7 failed', ic='play'),
  tile('rows today', '34,912', '+4.1% vs yesterday', ic='file'),
  tile('pending repairs', '2', 'awaiting approval', WARN, ic='branch'),
  tile('spend mtd', '$18.42', 'forecast $34 of $60', ic='zap'),
  panel(phead('Needs attention', smallbtn('Ack all') + smallbtn('Show muted')
              + btn('View all', 'ghost', href='Runs.dc.html', small=True), '3 open \u00b7 2 quiet')
        + '<div style="overflow:hidden;">%s</div>' % _attention),
  panel(phead('Runs, last 24 hours', '', '148 runs · 94.6% passed')
        + '<div style="padding:14px 14px 10px;flex:1 1 0;display:flex;flex-direction:column;justify-content:flex-end;">%s</div>' % _strip, grow=True),
  panel(phead('Live activity', iconbtn('pause', 'Pause the activity feed'))
        + '<div style="overflow:hidden;flex:1 1 0;">%s</div>' % _activity, grow=True, style='flex:1 1 0;'),
  panel(phead('Next scheduled') + '<div style="overflow:hidden;">%s</div>' % _sched),
  panel(phead('Agent spend, 7 days')
        + ('<div style="padding:12px;display:flex;flex-direction:column;gap:9px;">'
           + ''.join(
             '<div style="display:flex;flex-direction:column;gap:4px;">'
             '<div style="display:flex;justify-content:space-between;font-family:%s;font-size:11px;color:%s;">'
             '<span>%s</span><span style="color:%s;font-variant-numeric:tabular-nums;">%s</span></div>'
             '<div style="height:4px;background:%s;border-radius:2px;overflow:hidden;">'
             '<div style="width:%s;height:100%%;background:%s;"></div></div></div>' % (
               MONO, TXT3, n, TXT2, v, SURF2, w, c)
             for n, v, w, c in [('builder', '$3.90', '46%', AGENT), ('repair', '$2.61', '31%', AGENT),
                                ('fallback', '$1.44', '17%', ACC), ('audit', '$0.51', '6%', TXT3)])
           + '<div style="border-top:1px solid %s;margin-top:2px;padding-top:9px;display:flex;justify-content:space-between;">'
             '<span style="font-family:%s;font-size:11px;color:%s;">total</span>'
             '<span style="font-family:%s;font-size:12px;color:%s;font-weight:600;font-variant-numeric:tabular-nums;">$8.46</span></div>'
             % (LINE, MONO, TXT3, MONO, TXT)
           + '</div>'), grow=True))

add('Main.dc.html', 'Overview', 'Overview',
    pagehead('Overview', 'wed 17 sep · 12:06', btn('Run all due', 'ghost', 'play') + btn('New scraper', 'primary', 'plus', 'NewScraper.dc.html')),
    ov_body)

# ============================== 2. SCRAPERS ==============================
SCRAPERS = [
 ('ok','example-products','patchright','*/15 * * * *','2m ago','412','+0.7%','in 9m','v3','$0.00'),
 ('ok','hn-frontpage','http','*/15 * * * *','2m ago','30','0.0%','in 9m','v1','$0.00'),
 ('drift','shop-eu-prices','patchright+proxy','0 * * * *','1m ago','118','−74%','held','v7','$2.61'),
 ('ok','vendor-stock','patchright','0 * * * *','running','1,203','+1.2%','in 54m','v4','$0.00'),
 ('fail','news-archive','camoufox','0 6 * * *','6h ago','0','−100%','in 18h','v2','$0.44'),
 ('ok','competitor-skus','http','0 */6 * * *','11m ago','640','−31%','in 5h','v5','$0.00'),
 ('paused','job-board-eu','patchright','0 8 * * 1-5','3d ago','0','—','paused','v2','$0.00'),
 ('ok','gov-tenders','http','0 7 * * *','5h ago','88','+2.3%','in 19h','v1','$0.00'),
]
_rows = []
for st, name, eng, sched, last, rows_, delta, nxt, ver, spend in SCRAPERS:
    dc = FAIL if delta.startswith('−') and delta not in ('—',) else (TXT3 if delta in ('0.0%','—') else OK)
    _rows.append([
        status(st),
        alink(name, 'ScraperDetail.dc.html'),
        chip(eng, TXT3),
        mono(sched, 11, TXT3),
        mono(last, 11.5, ACC if last == 'running' else TXT2),
        mono(rows_, 12, TXT if rows_ != '0' else TXT3),
        mono(delta, 11.5, dc),
        mono(nxt, 11.5, WARN if nxt == 'held' else (TXT3 if nxt == 'paused' else TXT2)),
        chip(ver, AGENT if name == 'shop-eu-prices' else TXT3),
        mono(spend, 11.5, TXT2 if spend != '$0.00' else TXT3),
        ('<div style="display:flex;gap:4px;justify-content:flex-end;">%s%s%s</div>' % (
            iconbtn('play', 'Run %s now' % name), iconbtn('code', 'Edit %s script' % name, 'Script.dc.html'),
            iconbtn('gear', '%s settings' % name, 'ScraperDetail.dc.html'))),
    ])

_toolbar = ('<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">'
  '<div style="display:flex;align-items:center;gap:7px;height:30px;padding:0 10px;background:%s;'
  'border:1px solid %s;border-radius:4px;width:250px;box-sizing:border-box;">%s'
  '<label for="scr-q" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);">Search scrapers</label>'
  '<input id="scr-q" type="text" placeholder="filter by name, url or tag" style="flex-grow:1;background:transparent;'
  'border:0;outline:none;color:%s;font-family:%s;font-size:12px;width:100%%;"></div>'
  '%s%s%s%s<span style="flex-grow:1;"></span>%s%s</div>') % (
  SURF, LINE2, icon('search', 13, TXT3), TXT, MONO,
  btn('All 8', 'solid', small=False), btn('Healthy 6', 'ghost'), btn('Drift 1', 'ghost'), btn('Failed 1', 'ghost'),
  btn('Import YAML', 'ghost', 'download'), btn('New scraper', 'primary', 'plus', 'NewScraper.dc.html'))

scr_body = ('<div style="%s">%s%s</div>') % (
  PAD, _toolbar,
  panel(phead('Scrapers', '', '8 total · sorted by next run')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % table(
            [('','38px','left'),('Scraper','200px','left'),('Engine','130px','left'),('Schedule','120px','left'),
             ('Last run','90px','left'),('Rows','80px','right'),('Δ','70px','right'),('Next','80px','right'),
             ('Ver','56px','left'),('Spend 7d','80px','right'),('','108px','right')], _rows)
        + ('<div style="margin-top:auto;height:32px;flex-shrink:0;border-top:1px solid %s;background:%s;'
           'display:flex;align-items:center;padding:0 12px;gap:14px;">'
           '<span style="font-family:%s;font-size:11px;color:%s;">8 scrapers · 2,491 rows on last run · 7 scheduled</span>'
           '<span style="flex-grow:1;"></span>'
           '<a href="Audit.dc.html" style="font-family:%s;font-size:11px;color:%s;text-decoration:none;">%s 2 use custom_python</a>'
           '<span style="font-family:%s;font-size:11px;color:%s;">%s 1 needs approval</span></div>') % (
           LINE, SURF2, MONO, TXT3, MONO, AGENT, '◆', MONO, WARN, '▲'), grow=True))

add('Scrapers.dc.html', 'Scrapers', 'Scrapers',
    pagehead('Scrapers', '', ''), scr_body)

# ============================== 3. SCRAPER DETAIL ==============================
def kv(k, v, c=None):
    return ('<div style="display:flex;align-items:baseline;gap:10px;padding:6px 0;border-bottom:1px solid %s;">'
            '<span style="font-family:%s;font-size:11px;color:%s;width:96px;flex-shrink:0;">%s</span>'
            '<span style="font-family:%s;font-size:11.5px;color:%s;flex-grow:1;text-align:right;'
            'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">%s</span></div>') % (
        LINE, MONO, TXT3, esc(k), MONO, c or TXT, v)

_sd_runs = []
for rid, when, st, rows_, dur, eng, ver in [
    ('#4182','12:04','drift','118','41s','patchright+proxy','v7'),
    ('#4176','11:04','drift','131','38s','patchright+proxy','v7'),
    ('#4169','10:04','ok','452','29s','patchright','v7'),
    ('#4161','09:04','ok','448','31s','patchright','v7'),
    ('#4154','08:04','ok','455','28s','patchright','v7'),
    ('#4147','07:04','ok','451','30s','patchright','v7'),
    ('#4140','06:04','ok','449','27s','patchright','v7'),
    ('#4133','05:04','ok','453','33s','patchright','v7')]:
    _sd_runs.append([status(st), alink(rid,'RunDetail.dc.html',ACC), mono(when,11.5,TXT2),
                     mono(rows_,12,FAIL if st=='drift' else TXT), mono(dur,11.5,TXT2),
                     chip(eng,TXT3), chip(ver,TXT3)])

_spark = ''
for v, c in [(455,OK),(449,OK),(451,OK),(453,OK),(448,OK),(452,OK),(455,OK),(451,OK),(449,OK),(454,OK),
             (452,OK),(448,OK),(455,OK),(450,OK),(453,OK),(451,OK),(452,OK),(449,OK),(131,FAIL),(118,FAIL)]:
    _spark += '<div style="flex:1 1 0;height:%dpx;background:%s;border-radius:1px;align-self:flex-end;"></div>' % (max(3,int(v/455*52)), c)

_rules = ''
for r, v, state in [('min_rows','10','ok'),('max_rows','5,000','ok'),
                    ('row_count_band','last_5_runs ± 50%','fail'),
                    ('max_null_rate.price','0.05','fail'),('max_null_rate.url','0.00','ok'),
                    ('unique','url','ok')]:
    _rules += ('<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid %s;">'
               '<span style="color:%s;font-family:%s;font-size:10px;">%s</span>'
               '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;">%s</span>'
               '<span style="font-family:%s;font-size:11px;color:%s;">%s</span></div>') % (
        LINE, OK if state=='ok' else FAIL, MONO, '●' if state=='ok' else '✕',
        MONO, TXT2, esc(r), MONO, TXT3, esc(v))

_targets = ''
for t, d, st in [('webhook','https://ops.internal/hooks/prices','ok'),
                 ('s3','s3://scrape-archive/shop-eu/','ok'),
                 ('mcp','sheets-server · append_rows','fail')]:
    _targets += ('<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid %s;">'
                 '%s<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;overflow:hidden;'
                 'text-overflow:ellipsis;white-space:nowrap;">%s</span>%s</div>') % (
        LINE, chip(t, ACC if st=='ok' else FAIL), MONO, TXT2, esc(d),
        status('ok','') if st=='ok' else status('fail','2 retries'))

sd_body = ('<div style="%s">'
  '<div style="display:flex;gap:12px;flex-shrink:0;">%s%s%s%s%s</div>'
  '<div style="display:flex;gap:12px;flex:1 1 0;min-height:0;">'
    '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
    '<div style="width:320px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;overflow:hidden;">%s%s%s</div>'
  '</div></div>') % (
  PAD,
  tile('last run', '118 rows', 'validation failed', FAIL, ic='alert'),
  tile('7-day pass rate', '82%', '23 of 28 runs', WARN, ic='check'),
  tile('median duration', '31s', 'p95 58s', ic='clock'),
  tile('agent spend 7d', '$2.61', '1 build · 3 repairs', AGENT, ic='zap'),
  tile('total rows', '184,204', 'since 2 aug 2026', ic='db'),
  panel(phead('Row count, last 20 runs', '', 'band 226–678 expected')
        + ('<div style="padding:14px;flex:1 1 0;display:flex;flex-direction:column;justify-content:flex-end;gap:6px;">'
           '<div style="display:flex;align-items:flex-end;gap:3px;height:52px;">%s</div>'
           '<div style="display:flex;justify-content:space-between;font-family:%s;font-size:10px;color:%s;">'
           '<span>20 runs ago</span><span style="color:%s;">last 2 runs below band</span></div></div>') % (_spark, MONO, TXT3, FAIL)),
  panel(phead('Recent runs', btn('All runs', 'ghost', href='Runs.dc.html', small=True))
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % table(
            [('','34px','left'),('Run','70px','left'),('Time','62px','left'),('Rows','66px','right'),
             ('Dur','56px','right'),('Engine','140px','left'),('Ver','52px','left')], _sd_runs), grow=True),
  panel(phead('Configuration', iconbtn('gear', 'Edit configuration'))
        + '<div style="padding:4px 12px 10px;">%s%s%s%s%s%s%s</div>' % (
            kv('target', '<a href="#" style="text-decoration:none;color:%s;">shop.example.eu/pricing</a>' % ACC),
            kv('engine', chip('patchright', TXT2) + ' ' + chip('+ proxy', TXT2)),
            kv('schedule', 'hourly · 0 * * * *'),
            kv('profile', chip('shop-eu-login', TXT2)),
            kv('proxy pool', chip('eu-resi · sticky', TXT2)),
            kv('promotion', chip('auto_if_minor', WARN)),
            kv('fallback', chip('sonnet-5 · enabled', AGENT)))),
  panel(phead('Validation rules', '', '2 failing')
        + '<div style="padding:4px 12px 10px;">%s</div>' % _rules),
  panel(phead('Delivery targets', btn('Add', 'ghost', 'plus', 'Delivery.dc.html', small=True))
        + '<div style="padding:4px 12px 10px;">%s</div>' % _targets, grow=True))

_sd_actions = (btn('Run now', 'primary', 'play') + btn('Review repair v8', 'solid', 'branch', 'RepairReview.dc.html')
               + btn('Edit script', 'ghost', 'code', 'Script.dc.html') + iconbtn('pause', 'Pause this scraper')
               + iconbtn('trash', 'Delete this scraper', c=FAIL))
add('ScraperDetail.dc.html', 'Scraper detail', 'Scrapers',
    pagehead('shop-eu-prices', '', _sd_actions,
             tabs=[('Overview',True),('Script',False),('Runs',False),('Records',False),('Delivery',False),('Settings',False)]),
    sd_body)

# ============================== 4. RECORDS ==============================
_rec_rows = []
_recs = [
 ('2026-09-17 12:04','#4182','Lavazza Qualita Rossa 1kg','','—','llm','shop-eu-prices'),
 ('2026-09-17 12:04','#4182','Illy Classico Beans 1kg','','—','llm','shop-eu-prices'),
 ('2026-09-17 12:04','#4182','Segafredo Intermezzo 1kg','12.49','EUR','llm','shop-eu-prices'),
 ('2026-09-17 11:04','#4176','Lavazza Crema e Gusto 1kg','9.85','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Kimbo Napoletano 1kg','11.20','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Pellini Top 1kg','13.75','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Vergnano 1882 1kg','14.10','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Borbone Miscela Rossa 1kg','10.95','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Lavazza Super Crema 1kg','15.40','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Illy Intenso Beans 1kg','16.90','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Caffe Molinari Qualita Oro','12.30','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Danesi Caffe Gold 1kg','17.25','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Trombetta Bar 1kg','9.40','EUR','script','shop-eu-prices'),
 ('2026-09-17 10:04','#4169','Hausbrandt Espresso 1kg','13.95','EUR','script','shop-eu-prices'),
 ('2026-09-17 09:04','#4161','Lavazza Tierra Bio 1kg','14.85','EUR','script','shop-eu-prices'),
 ('2026-09-17 09:04','#4161','Saquella Bar Gold 1kg','11.60','EUR','script','shop-eu-prices'),
]
for ts, run, name, price, cur, src, scr in _recs:
    _rec_rows.append([
        mono(ts, 11, TXT3), alink(run, 'RunDetail.dc.html', ACC, 11.5),
        '<span style="font-family:%s;font-size:12px;color:%s;">%s</span>' % (MONO, TXT, esc(name)),
        mono(price if price else 'null', 12, TXT if price else FAIL),
        mono(cur, 11.5, TXT3),
        chip('llm', AGENT) if src == 'llm' else chip('script', TXT3),
        mono(scr, 11, TXT2)])

_qbar = ('<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">'
  '<div style="display:flex;align-items:center;gap:7px;height:30px;padding:0 10px;background:%s;border:1px solid %s;'
  'border-radius:4px;flex-grow:1;box-sizing:border-box;">%s'
  '<label for="rec-q" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);">Query records</label>'
  '<input id="rec-q" type="text" value="scraper = shop-eu-prices and price is null order by ts desc"'
  ' style="flex-grow:1;background:transparent;border:0;outline:none;color:%s;font-family:%s;font-size:12px;"></div>'
  '%s%s%s%s%s</div>') % (SURF, LINE2, icon('search', 13, TXT3), TXT, MONO,
  btn('Run query', 'primary', 'play'), btn('Schema', 'ghost', 'file', 'Schema.dc.html'),
  btn('Last 7 days', 'ghost', 'calendar'),
  btn('Export CSV', 'ghost', 'download'), btn('Parquet', 'ghost', 'download'))

rec_body = ('<div style="%s">%s%s'
  '<div style="display:flex;gap:8px;flex-shrink:0;">%s%s%s%s</div>%s</div>') % (
  PAD, _qbar,
  banner('drift', 'Last clean run was 2 hours ago. The 3 newest rows came from the LLM fallback, not the script. '
                  'Anything read after 12:04 is provisional until repair v8 is approved.',
         smallbtn('Review v8', ACC, 'RepairReview.dc.html') + smallbtn('Hide fallback rows')),
  chip('16 rows returned', TXT2), chip('3 null price', FAIL), chip('3 from llm fallback', AGENT), chip('query 41 ms', TXT3),
  panel(phead('Records', iconbtn('copy', 'Copy results as JSON') + iconbtn('ext', 'Open in new tab'),
              'shop-eu-prices · schema v7')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % table(
            [('Extracted at','130px','left'),('Run','64px','left'),('name','380px','left'),
             ('price','90px','right'),('currency','80px','left'),('Source','80px','left'),('Scraper','160px','left')],
            _rec_rows, zebra=True)
        + ('<div style="margin-top:auto;height:32px;flex-shrink:0;border-top:1px solid %s;background:%s;display:flex;'
           'align-items:center;padding:0 12px;gap:10px;">'
           '<span style="font-family:%s;font-size:11px;color:%s;">showing 1–16 of 16</span>'
           '<span style="flex-grow:1;"></span>%s%s</div>') % (LINE, SURF2, MONO, TXT3,
           btn('Prev', 'ghost', small=True), btn('Next', 'ghost', small=True)), grow=True))

add('Records.dc.html', 'Records', 'Records',
    pagehead('Records', '184,204 stored', ''), rec_body)

# ---------- syntax + log helpers ----------
SY_KEY='#5CC9C9'; SY_STR='#C9A46A'; SY_NUM='#D98E73'; SY_COM='#69738A'; SY_PUN='#7A8599'
import re as _re
def _escq(t): return html.escape(str(t))
def _sy(line):
    if line.strip().startswith('#'):
        return '<span style="color:%s;">%s</span>' % (SY_COM, _escq(line))
    out = ''
    m = _re.match(r'^(\s*-?\s*)([A-Za-z_][\w\.]*)(:)(.*)$', line)
    if m:
        ind, key, col, rest = m.groups()
        out += '<span style="color:%s;">%s</span>' % (SY_PUN, _escq(ind).replace(' ', '&nbsp;'))
        out += '<span style="color:%s;">%s</span><span style="color:%s;">%s</span>' % (SY_KEY, _escq(key), SY_PUN, col)
        rest_h = _escq(rest)
        rest_h = _re.sub(r'(&quot;[^&]*&quot;|&#x27;[^&]*&#x27;)', r'<span style="color:%s;">\1</span>' % SY_STR, rest_h)
        rest_h = _re.sub(r'\b(\d+(?:\.\d+)?)\b', r'<span style="color:%s;">\1</span>' % SY_NUM, rest_h)
        rest_h = _re.sub(r'\b(true|false|null)\b', r'<span style="color:%s;">\1</span>' % SY_NUM, rest_h)
        out += rest_h.replace('  ', '&nbsp;&nbsp;')
        return out
    h = _escq(line).replace(' ', '&nbsp;')
    h = _re.sub(r'(&quot;[^&]*&quot;)', r'<span style="color:%s;">\1</span>' % SY_STR, h)
    return '<span style="color:%s;">%s</span>' % (TXT2, h)

def code_block(src, start=1, height=None, marks=None):
    marks = marks or {}
    rows = ''
    for i, ln in enumerate(src.split('\n')):
        n = start + i
        bg = marks.get(n, 'transparent')
        rows += ('<div style="display:flex;background:%s;min-height:18px;">'
                 '<span style="width:34px;flex-shrink:0;text-align:right;padding-right:10px;color:%s;'
                 'font-family:%s;font-size:11px;line-height:18px;user-select:none;font-variant-numeric:tabular-nums;">%d</span>'
                 '<span style="font-family:%s;font-size:11.5px;line-height:18px;white-space:pre;color:%s;">%s</span>'
                 '</div>') % (bg, TXT3, MONO, n, MONO, TXT, _sy(ln))
    h = ('height:%dpx;' % height) if height else 'flex:1 1 0;'
    return '<div style="%soverflow:hidden;padding:8px 0;background:%s;">%s</div>' % (h, BG, rows)

LVL = {'info': TXT2, 'ok': OK, 'warn': WARN, 'err': FAIL, 'step': ACC, 'agent': AGENT, 'dim': TXT3}
def logline(ts, lvl, tag, text):
    return ('<div style="display:flex;gap:9px;padding:1.5px 12px;font-family:%s;font-size:11px;line-height:16px;">'
            '<span style="color:%s;flex-shrink:0;font-variant-numeric:tabular-nums;">%s</span>'
            '<span style="color:%s;flex-shrink:0;width:64px;font-weight:500;">%s</span>'
            '<span style="color:%s;white-space:pre-wrap;">%s</span></div>') % (
        MONO, TXT3, ts, LVL[lvl], esc(tag), LVL['info'] if lvl in ('info','step') else LVL[lvl], esc(text))

# ============================== 5. SCRIPT & VERSIONS ==============================
YAML_V7 = """# shop-eu-prices · active version
version: 7
engine: browser
stealth: patchright
profile: shop-eu-login
proxy: eu-resi
escalation: [http, patchright, patchright+proxy, camoufox, byparr]
rate_limit: {min_delay_s: 2, max_delay_s: 6}

steps:
  - op: goto
    url: "https://shop.example.eu/pricing?page={{page}}"
  - op: wait_for
    selector: "css=.product-card"
    timeout_ms: 15000
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields:
      name:
        selector: "h3.title"
        attr: text
      price:
        selector: ".price"
        attr: text
        parse: money
      currency:
        selector: ".price [data-cur]"
        attr: text
      url:
        selector: "a.detail"
        attr: href
        absolute: true
  - op: paginate
    next: "css=a[rel=next]"
    max_pages: 20
    var: page
  - op: emit
    from: products

validation:
  min_rows: 10
  max_rows: 5000
  row_count_band:
    relative_to: last_5_runs
    tolerance: 0.5
  max_null_rate:
    price: 0.05
    url: 0.0
  unique: [url]"""

_vers = ''
for v, who, when, st, note in [
    ('v8','repair','2m ago','pending','selector fix for .price'),
    ('v7','repair','14d ago','active','pagination guard'),
    ('v6','human','21d ago','retired','rate limit raised'),
    ('v5','repair','28d ago','retired','currency field added'),
    ('v4','repair','35d ago','retired','wait_for timeout 15s'),
    ('v3','human','41d ago','retired','proxy pool eu-resi'),
    ('v2','repair','46d ago','retired','title selector'),
    ('v1','builder','2 aug 2026','retired','initial build')]:
    on = (v == 'v7')
    wc = {'repair': AGENT, 'builder': AGENT, 'human': TXT2}[who]
    sc = {'pending': WARN, 'active': OK, 'retired': TXT3}[st]
    _vers += ('<a href="%s" style="display:flex;flex-direction:column;gap:3px;padding:8px 11px;border-bottom:1px solid %s;'
              'background:%s;text-decoration:none;border-left:2px solid %s;">'
              '<div style="display:flex;align-items:center;gap:7px;">'
              '<span style="font-family:%s;font-size:12px;font-weight:600;color:%s;">%s</span>'
              '%s<span style="flex-grow:1;"></span>'
              '<span style="font-family:%s;font-size:10px;color:%s;">%s</span></div>'
              '<div style="font-family:%s;font-size:10.5px;color:%s;overflow:hidden;text-overflow:ellipsis;'
              'white-space:nowrap;">%s</div>'
              '<span style="font-family:%s;font-size:10px;color:%s;">%s</span></a>') % (
        'RepairReview.dc.html' if v == 'v8' else '#', LINE, SURF3 if on else 'transparent',
        ACC if on else 'transparent', MONO, TXT if on else TXT2, v, chip(who, wc), MONO, sc, st,
        MONO, TXT3, esc(note), MONO, TXT3, when)

_testpanel = ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:9px;">'
  '<div style="display:flex;gap:6px;">%s%s</div>'
  '<div style="border:1px solid %s;border-radius:4px;background:%s;padding:9px 10px;display:flex;flex-direction:column;gap:6px;">'
  '<div style="display:flex;align-items:center;gap:7px;">%s<span style="font-family:%s;font-size:11px;color:%s;">'
  'last test 4 min ago</span></div>%s%s%s%s</div></div>') % (
  btn('Test run', 'primary', 'play'), btn('Validate only', 'ghost', 'check'),
  LINE, SURF2, status('ok', 'passed'), MONO, TXT3,
  kv('rows', mono('452', 11.5, OK)), kv('duration', mono('29s', 11.5)),
  kv('null rate price', mono('0.00', 11.5, OK)), kv('engine', chip('patchright', TXT3)))

scr_actions = (btn('Save version', 'primary', 'check') + btn('Diff vs v6', 'ghost', 'branch')
               + btn('Test run', 'ghost', 'play') + iconbtn('download', 'Download YAML') + iconbtn('trash', 'Delete version', c=FAIL))

script_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="width:212px;flex-shrink:0;display:flex;">%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:300px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div>') % (
  panel(phead('Versions', iconbtn('plus', 'New version from active'))
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % _vers, grow=True, style='flex:1 1 0;'),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:10px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:500;">scrapers/shop-eu-prices.yaml</span>'
         '%s<span style="flex-grow:1;"></span>'
         '<span style="font-family:%s;font-size:10.5px;color:%s;">yaml · utf-8 · 52 lines</span>%s</div>') % (
        LINE, SURF2, MONO, TXT, chip('v7 active', OK), MONO, TXT3, iconbtn('ext', 'Open in editor'))
        + code_block(YAML_V7), grow=True, style='flex:1 1 0;'),
  panel(phead('Test & promote', '', 'promotion auto_if_minor') + _testpanel),
  panel(phead('Step reference', '', '17 ops')
        + ('<div style="padding:9px 12px;display:flex;flex-wrap:wrap;gap:5px;align-content:flex-start;flex:1 1 0;overflow:hidden;">%s</div>'
           % ''.join(chip(o, ACC if o in ('extract_list','paginate','emit','goto','wait_for') else TXT3)
                     for o in ['goto','wait_for','click','fill','select','scroll','hover','press','extract',
                               'extract_list','paginate','loop','emit','sleep','screenshot','solve_challenge',
                               'assert','custom_python'])
           + ('<div style="width:100%%;margin-top:7px;padding-top:9px;border-top:1px solid %s;font-family:%s;'
              'font-size:10.5px;color:%s;line-height:1.5;">%s <span style="color:%s;">custom_python</span> runs '
              'unsandboxed in the run subprocess. Any version that adds or changes one needs human approval, '
              'whatever the promotion policy.</div>') % (LINE, MONO, TXT3, icon('alert', 11, WARN), WARN)),
        grow=True))

add('Script.dc.html', 'Script & versions', 'Scrapers',
    pagehead('shop-eu-prices', 'script v7', scr_actions,
             tabs=[('Overview',False),('Script',True),('Runs',False),('Records',False),('Delivery',False),('Settings',False)]),
    script_body)

# ============================== 6. RUNS ==============================
RUNS = [
 ('run','#4188','vendor-stock','v4','12:06:14','—','—','patchright','1','—','—'),
 ('run','#4187','example-products','v3','12:06:02','—','—','patchright','1','—','—'),
 ('drift','#4182','shop-eu-prices','v7','12:04:01','41s','118','patchright+proxy','3','$0.31','held'),
 ('ok','#4186','hn-frontpage','v1','12:04:09','2.1s','30','http','1','—','2 of 2'),
 ('ok','#4185','example-products','v3','12:02:55','24s','412','patchright','1','—','1 of 1'),
 ('ok','#4184','gov-tenders','v1','11:58:12','6.4s','88','http','1','—','1 of 1'),
 ('drift','#4183','competitor-skus','v5','11:55:40','1m12s','640','patchright+proxy','3','$0.18','held'),
 ('fail','#4180','news-archive','v2','11:52:03','2m04s','0','camoufox','4','$0.12','—'),
 ('ok','#4179','example-products','v3','11:47:19','26s','409','patchright','1','—','1 of 1'),
 ('ok','#4178','vendor-stock','v4','11:45:00','1m31s','1,203','patchright','1','—','2 of 2'),
 ('ok','#4177','hn-frontpage','v1','11:41:22','1.9s','30','http','1','—','2 of 2'),
 ('ok','#4175','competitor-skus','v5','11:36:10','58s','640','http','1','—','1 of 1'),
 ('ok','#4174','example-products','v3','11:31:52','25s','414','patchright','1','—','1 of 1'),
 ('ok','#4173','gov-tenders','v1','11:30:07','5.8s','88','http','1','—','1 of 1'),
 ('fail','#4172','shop-eu-prices','v7','11:27:41','30s','0','patchright+proxy','2','—','—'),
 ('ok','#4171','vendor-stock','v4','11:26:18','1m28s','1,189','patchright','1','—','2 of 2'),
 ('ok','#4170','hn-frontpage','v1','11:22:55','2.0s','30','http','1','—','2 of 2'),
 ('queued','#4169','news-archive','v2','11:19:30','—','—','camoufox','—','—','—'),
 ('ok','#4168','example-products','v3','11:16:02','27s','414','patchright','1','—','1 of 1'),
 ('ok','#4167','competitor-skus','v5','11:12:38','54s','912','http','1','—','1 of 1'),
 ('ok','#4166','gov-tenders','v1','11:10:01','6.1s','86','http','1','—','1 of 1'),
 ('ok','#4165','vendor-stock','v4','11:06:44','1m26s','1,201','patchright','1','—','2 of 2'),
]
_run_rows = []
for st, rid, scr, ver, start, dur, rows_, eng, esc_, cost, deliv in RUNS:
    _run_rows.append([
        cbox(rid in ('#4180', '#4172', '#4183')), status(st), alink(rid, 'RunLive.dc.html' if st == 'run' else 'RunDetail.dc.html', ACC, 11.5),
        alink(scr, 'ScraperDetail.dc.html', TXT, 11.5), chip(ver, TXT3),
        mono(start, 11.5, TXT2), mono(dur, 11.5, TXT2),
        mono(rows_, 12, TXT3 if rows_ in ('—', '0') else TXT),
        chip(eng, TXT3), mono(esc_, 11.5, WARN if esc_ not in ('1', '—') else TXT3),
        mono(cost, 11.5, AGENT if cost != '—' else TXT3),
        mono(deliv, 11.5, WARN if deliv == 'held' else (TXT3 if deliv == '—' else TXT2))])

_runs_toolbar = ('<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">%s%s%s%s%s%s'
  '<span style="flex-grow:1;"></span>%s%s</div>') % (
  btn('All scrapers', 'solid', 'filter'), btn('Any status', 'ghost', 'chev'), btn('Any engine', 'ghost', 'chev'),
  btn('Today', 'ghost', 'calendar'), btn('Failed only', 'ghost'), btn('With repairs', 'ghost'),
  btn('Export', 'ghost', 'download'), iconbtn('refresh', 'Refresh run list'))

runs_body = ('<div style="%s">%s'
  '<div style="display:flex;gap:12px;flex-shrink:0;">%s%s%s%s%s</div>%s</div>') % (
  PAD, _runs_toolbar,
  tile('runs today', '148', 'across 8 scrapers', ic='play'),
  tile('passed', '141', '95.3%', OK, ic='check'),
  tile('validation failed', '5', '3 repairs raised', WARN, ic='alert'),
  tile('blocked', '2', 'news-archive · cf', FAIL, ic='shield'),
  tile('median duration', '27s', 'p95 1m34s', ic='clock'),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:9px;'
         'border-bottom:1px solid %s;background:%s;">%s'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">3 selected</span>'
         '<span style="width:1px;height:15px;background:%s;"></span>%s%s%s%s'
         '<span style="flex-grow:1;"></span>'
         '<span style="font-family:%s;font-size:11px;color:%s;">live · newest first</span></div>') % (
        LINE, SURF3, cbox(True), SANS, TXT, LINE2,
        btn('Re-run 3', 'solid', 'refresh', small=True), btn('Re-deliver', 'ghost', 'send', small=True),
        btn('Export', 'ghost', 'download', small=True), btn('Clear', 'ghost', small=True), MONO, TXT3)
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % table(
            [('','28px','left'),('','32px','left'),('Run','64px','left'),('Scraper','166px','left'),('Ver','50px','left'),
             ('Started','80px','left'),('Duration','76px','right'),('Rows','72px','right'),('Engine','140px','left'),
             ('Esc','48px','right'),('Agent cost','82px','right'),('Delivered','80px','right')], _run_rows)
        + ('<div style="margin-top:auto;height:32px;flex-shrink:0;border-top:1px solid %s;background:%s;display:flex;'
           'align-items:center;padding:0 12px;gap:10px;">'
           '<span style="font-family:%s;font-size:11px;color:%s;">showing 22 of 148 today</span>'
           '<span style="flex-grow:1;"></span>%s%s</div>') % (LINE, SURF2, MONO, TXT3,
           btn('Prev', 'ghost', small=True), btn('Next', 'ghost', small=True)), grow=True))

add('Runs.dc.html', 'Runs', 'Runs', pagehead('Runs', '', ''), runs_body)

# ============================== 7. RUN DETAIL ==============================
def step_row(st, op, detail, dur, active=False):
    g, c, _ = ST[st]
    return ('<div style="display:flex;align-items:flex-start;gap:8px;padding:6px 11px;border-bottom:1px solid %s;'
            'background:%s;">'
            '<span style="color:%s;font-family:%s;font-size:10px;line-height:15px;flex-shrink:0;">%s</span>'
            '<div style="flex-grow:1;min-width:0;display:flex;flex-direction:column;gap:2px;">'
            '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:500;">%s</span>'
            '<span style="font-family:%s;font-size:10.5px;color:%s;overflow:hidden;text-overflow:ellipsis;'
            'white-space:nowrap;">%s</span></div>'
            '<span style="font-family:%s;font-size:10.5px;color:%s;flex-shrink:0;font-variant-numeric:tabular-nums;">%s</span>'
            '</div>') % (LINE, SURF3 if active else 'transparent', c, MONO, g, MONO,
                         TXT if not active else ACC, esc(op), MONO, TXT3, esc(detail), MONO, TXT3, esc(dur))

_rd_steps = ''.join([
    step_row('ok','goto','shop.example.eu/pricing?page=1','1.9s'),
    step_row('ok','wait_for','css=.product-card · 42 matched','0.4s'),
    step_row('ok','extract_list','products · 42 rows','0.3s'),
    step_row('ok','paginate','page 2 of 3','1.7s'),
    step_row('ok','extract_list','products · 42 rows','0.3s'),
    step_row('ok','paginate','page 3 of 3','1.6s'),
    step_row('ok','extract_list','products · 34 rows','0.2s'),
    step_row('ok','emit','118 rows total','0.1s'),
    step_row('fail','validate','2 of 6 rules failed','0.1s'),
    step_row('agent','fallback','sonnet-5 recovered 118 rows','8.4s'),
    step_row('agent','repair','candidate v8 proposed','21.0s'),
])

_rd_log = ''.join([
    logline('12:04:01','step','runner','starting run #4182 · shop-eu-prices v7 · pid 20814'),
    logline('12:04:01','info','engine','patchright 1.62.3 · chromium 153 · headed · persistent context'),
    logline('12:04:01','info','proxy','eu-resi · sticky · 85.203.x.x · NL'),
    logline('12:04:01','info','profile','shop-eu-login · storage_state restored · 14 cookies'),
    logline('12:04:03','ok','goto','200 · shop.example.eu/pricing?page=1 · 1.9s'),
    logline('12:04:03','ok','wait_for','css=.product-card · 42 matched · 412ms'),
    logline('12:04:04','warn','extract','field price · selector .price matched 42, 28 empty'),
    logline('12:04:04','ok','extract','42 rows · name 42/42 · price 14/42 · url 42/42'),
    logline('12:04:06','ok','paginate','page 2 of 3'),
    logline('12:04:07','warn','extract','field price · selector .price matched 42, 27 empty'),
    logline('12:04:08','ok','paginate','page 3 of 3'),
    logline('12:04:09','warn','extract','field price · selector .price matched 34, 23 empty'),
    logline('12:04:09','ok','emit','118 rows'),
    logline('12:04:09','err','validate','max_null_rate.price · measured 0.66 · expected ≤ 0.05 · FAIL'),
    logline('12:04:09','err','validate','row_count_band · measured 118 · expected 226–678 · FAIL'),
    logline('12:04:09','err','runner','run marked validation_failed · delivery held'),
    logline('12:04:10','agent','fallback','sonnet-5 · extracting from captured html · 3 chunks'),
    logline('12:04:18','agent','fallback','118 rows · price 118/118 · flagged source=llm_fallback'),
    logline('12:04:18','ok','deliver','webhook ops.internal · 118 rows · 202'),
    logline('12:04:18','ok','deliver','s3://scrape-archive/shop-eu/4182.parquet · 41 KB'),
    logline('12:04:19','err','deliver','mcp sheets-server · append_rows · timeout · retry 2 of 5'),
    logline('12:04:20','agent','repair','opus-5 · reading failed run + last good fixture'),
    logline('12:04:29','agent','repair','probe [data-testid=price-now] → 118/118 non-empty'),
    logline('12:04:41','agent','repair','candidate v8 written · 1 selector changed · minor'),
    logline('12:04:41','warn','repair','policy auto_if_minor · test run passed · awaiting approval'),
])

_rd_valid = ''
for r, meas, exp, ok_ in [('max_null_rate.price','0.66','≤ 0.05',False),
                          ('row_count_band','118','226–678',False),
                          ('min_rows','118','≥ 10',True),
                          ('max_rows','118','≤ 5,000',True),
                          ('max_null_rate.url','0.00','≤ 0.00',True),
                          ('unique.url','118 distinct','118',True)]:
    _rd_valid += ('<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid %s;">'
      '<span style="color:%s;font-family:%s;font-size:10px;">%s</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;">%s</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;font-variant-numeric:tabular-nums;">%s</span>'
      '<span style="font-family:%s;font-size:10.5px;color:%s;width:64px;text-align:right;">%s</span></div>') % (
      LINE, OK if ok_ else FAIL, MONO, '●' if ok_ else '✕', MONO, TXT2, esc(r),
      MONO, TXT if ok_ else FAIL, esc(meas), MONO, TXT3, esc(exp))

def fake_page(w=282, h=170, blocked=False):
    if blocked:
        inner = ('<div style="flex:1 1 0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;">'
                 '%s<span style="font-family:%s;font-size:10px;color:%s;">Checking your browser…</span>'
                 '<div style="width:70px;height:4px;border-radius:2px;background:#2A2018;overflow:hidden;">'
                 '<div style="width:45%%;height:100%%;background:%s;"></div></div></div>') % (
            icon('shield', 20, WARN), MONO, WARN, WARN)
    else:
        cards = ''
        for i in range(6):
            cards += ('<div style="flex:1 1 30%%;min-width:0;background:%s;border:1px solid %s;border-radius:2px;'
                      'padding:5px;display:flex;flex-direction:column;gap:3px;">'
                      '<div style="height:20px;background:%s;border-radius:1px;"></div>'
                      '<div style="height:4px;width:80%%;background:%s;border-radius:1px;"></div>'
                      '<div style="height:4px;width:38%%;background:%s;border-radius:1px;"></div></div>') % (
                SURF2, LINE, SURF3, LINE2, ACCD if i % 2 else LINE)
        inner = ('<div style="flex:1 1 0;padding:7px;display:flex;flex-wrap:wrap;gap:5px;align-content:flex-start;">%s</div>' % cards)
    return ('<div style="width:%dpx;height:%dpx;background:%s;border:1px solid %s;border-radius:3px;'
            'display:flex;flex-direction:column;overflow:hidden;box-sizing:border-box;">'
            '<div style="height:16px;flex-shrink:0;background:%s;border-bottom:1px solid %s;display:flex;'
            'align-items:center;gap:4px;padding:0 6px;">'
            '<span style="width:5px;height:5px;border-radius:50%%;background:%s;"></span>'
            '<span style="width:5px;height:5px;border-radius:50%%;background:%s;"></span>'
            '<span style="flex-grow:1;height:7px;background:%s;border-radius:3px;"></span></div>%s</div>') % (
        w, h, SURF, LINE, SURF2, LINE, LINE2, LINE2, BG, inner)

rd_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="width:262px;flex-shrink:0;display:flex;">%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:330px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;overflow:hidden;">%s%s%s</div></div>') % (
  panel(phead('Steps', '', '11 · 41s') + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % _rd_steps,
        grow=True, style='flex:1 1 0;'),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:8px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">Log</span>%s%s%s'
         '<span style="flex-grow:1;"></span>%s%s</div>') % (LINE, SURF2, SANS, TXT2,
        chip('all 25', TXT2), chip('warn 4', WARN), chip('error 4', FAIL),
        iconbtn('download', 'Download log'), iconbtn('copy', 'Copy log'))
        + '<div style="flex:1 1 0;overflow:hidden;padding:7px 0;background:%s;">%s</div>' % (BG, _rd_log),
        grow=True, style='flex:1 1 0;'),
  panel(phead('Validation', chip('2 failed', FAIL), 'v7 rules')
        + '<div style="padding:4px 12px 10px;">%s</div>' % _rd_valid),
  panel(phead('Artifacts', '', '4 files')
        + ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:9px;">'
           '%s<div style="display:flex;flex-direction:column;gap:5px;">%s</div></div>') % (
           fake_page(),
           ''.join('<a href="#" style="display:flex;align-items:center;gap:7px;padding:4px 0;text-decoration:none;">'
                   '%s<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;">%s</span>'
                   '<span style="font-family:%s;font-size:10.5px;color:%s;">%s</span></a>' % (
                       icon(i, 12, TXT3), MONO, TXT2, n, MONO, TXT3, s)
                   for i, n, s in [('image','screen.png','184 KB'), ('file','page.html','612 KB'),
                                   ('file','records.jsonl','41 KB'), ('zap','trace.zip','2.1 MB')]))),
  panel(phead('Cost & delivery')
        + '<div style="padding:4px 12px 10px;">%s%s%s%s%s</div>' % (
            kv('fallback', mono('$0.09', 11.5, AGENT) + ' ' + chip('sonnet-5', TXT3)),
            kv('repair', mono('$0.22', 11.5, AGENT) + ' ' + chip('opus-5', TXT3)),
            kv('webhook', status('ok', '202')),
            kv('s3', status('ok', '41 KB')),
            kv('mcp sheets', status('fail', 'retry 2/5'))), grow=True))

rd_actions = (btn('Review repair v8', 'primary', 'branch', 'RepairReview.dc.html') + btn('Re-run', 'ghost', 'refresh')
              + btn('Open trace', 'ghost', 'ext') + iconbtn('download', 'Download all artifacts'))
add('RunDetail.dc.html', 'Run detail', 'Runs',
    pagehead('Run #4182', 'shop-eu-prices · v7 · 17 sep 12:04:01', rd_actions,
             tabs=[('Summary',True),('Log',False),('Records 118',False),('Metrics',False),('Artifacts',False)]),
    rd_body)

# ============================== 8. RUN LIVE ==============================
_rl_steps = ''.join([
    step_row('ok','goto','shop.example.eu — vendor list','2.1s'),
    step_row('ok','wait_for','css=tr.stock-row · 120 matched','0.5s'),
    step_row('ok','extract_list','stock · 120 rows','0.4s'),
    step_row('ok','paginate','page 2 of 11','1.8s'),
    step_row('ok','extract_list','stock · 120 rows','0.3s'),
    step_row('run','paginate','page 3 of 11 · in progress','—', active=True),
    step_row('queued','extract_list','stock','—'),
    step_row('queued','paginate','pages 4–11','—'),
    step_row('queued','emit','stock','—'),
    step_row('queued','validate','6 rules','—'),
])
_rl_log = ''.join([
    logline('12:06:14','step','runner','starting run #4188 · vendor-stock v4 · pid 20902'),
    logline('12:06:14','info','engine','patchright 1.62.3 · chromium 153 · headless=false'),
    logline('12:06:14','info','proxy','none · direct'),
    logline('12:06:16','ok','goto','200 · shop.example.eu/vendors/stock · 2.1s'),
    logline('12:06:17','ok','wait_for','css=tr.stock-row · 120 matched · 503ms'),
    logline('12:06:17','ok','extract','120 rows · sku 120/120 · qty 120/120 · eta 118/120'),
    logline('12:06:19','ok','paginate','page 2 of 11'),
    logline('12:06:19','ok','extract','120 rows · sku 120/120 · qty 120/120 · eta 120/120'),
    logline('12:06:21','info','rate','sleeping 2.4s · rate_limit 2–6s'),
    logline('12:06:23','step','paginate','page 3 of 11 …'),
])
_rl_log += ('<div style="display:flex;gap:9px;padding:1.5px 12px;font-family:%s;font-size:11px;line-height:16px;">'
  '<span style="color:%s;">12:06:24</span><span style="color:%s;width:64px;font-weight:500;">wait_for</span>'
  '<span style="color:%s;">css=tr.stock-row<span style="display:inline-block;width:7px;height:12px;'
  'background:%s;margin-left:3px;vertical-align:middle;"></span></span></div>') % (MONO, TXT3, ACC, TXT2, ACC)

rl_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="width:262px;flex-shrink:0;display:flex;">%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:330px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div>') % (
  panel(phead('Steps', status('run', '6 of 10'), '')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % _rl_steps, grow=True, style='flex:1 1 0;'),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:8px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">Log</span>%s'
         '<span style="flex-grow:1;"></span>%s%s</div>') % (LINE, SURF2, SANS, TXT2,
        chip('streaming · sse', ACC), btn('Follow', 'solid', 'chev', small=True), iconbtn('pause', 'Pause log'))
        + '<div style="flex:1 1 0;overflow:hidden;padding:7px 0;background:%s;">%s</div>' % (BG, _rl_log),
        grow=True, style='flex:1 1 0;'),
  panel(phead('Browser', chip('live · 1 fps', ACC), 'context 1 of 1')
        + ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:9px;">%s'
           '<div style="font-family:%s;font-size:10.5px;color:%s;overflow:hidden;text-overflow:ellipsis;'
           'white-space:nowrap;">shop.example.eu/vendors/stock?page=3</div></div>') % (
           fake_page(w=304, h=184), MONO, TXT3)),
  panel(phead('Progress')
        + ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:11px;flex:1 1 0;">'
           '<div style="display:flex;flex-direction:column;gap:5px;">'
           '<div style="display:flex;justify-content:space-between;font-family:%s;font-size:11px;">'
           '<span style="color:%s;">pages</span><span style="color:%s;">3 of 11</span></div>'
           '<div style="height:5px;background:%s;border-radius:3px;overflow:hidden;">'
           '<div style="width:27%%;height:100%%;background:%s;"></div></div></div>'
           '%s%s%s%s%s'
           '<div style="margin-top:auto;display:flex;gap:6px;">%s%s</div></div>') % (
           MONO, TXT3, TXT, SURF2, ACC,
           kv('elapsed', mono('10s', 11.5)), kv('rows so far', mono('240', 11.5)),
           kv('expected', mono('~1,200', 11.5, TXT2)), kv('engine', chip('patchright', TXT3)),
           kv('escalation', chip('1 of 5 · direct', TXT3)),
           btn('Cancel run', 'danger', 'stop'), btn('Open trace', 'ghost', 'ext')), grow=True))

add('RunLive.dc.html', 'Run live', 'Runs',
    pagehead('Run #4188', 'vendor-stock · v4 · started 12:06:14',
             status('run', 'running') + btn('Cancel', 'danger', 'stop')), rl_body)

# ============================== 9. NEW SCRAPER ==============================
def field(lab, control, hint=''):
    h = '<div style="font-family:%s;font-size:10.5px;color:%s;line-height:1.45;">%s</div>' % (MONO, TXT3, esc(hint)) if hint else ''
    return ('<div style="display:flex;flex-direction:column;gap:5px;">'
            '<label style="font-family:%s;font-size:11px;color:%s;font-weight:500;">%s</label>%s%s</div>') % (
        SANS, TXT2, esc(lab), control, h)

def inp(value='', ph='', mono_f=True, w='100%'):
    return ('<input type="text" value="%s" placeholder="%s" style="height:32px;width:%s;box-sizing:border-box;'
            'padding:0 10px;background:%s;border:1px solid %s;border-radius:4px;color:%s;outline:none;'
            'font-family:%s;font-size:12px;">') % (esc(value), esc(ph), w, SURF2, LINE2, TXT, MONO if mono_f else SANS)

def area(value='', rows=3):
    return ('<textarea rows="%d" style="width:100%%;box-sizing:border-box;padding:8px 10px;background:%s;'
            'border:1px solid %s;border-radius:4px;color:%s;outline:none;resize:none;font-family:%s;'
            'font-size:12px;line-height:1.55;">%s</textarea>') % (rows, SURF2, LINE2, TXT, MONO, esc(value))

def seg(options, active):
    out = ''
    for o in options:
        on = (o == active)
        out += ('<span style="display:inline-flex;align-items:center;height:30px;padding:0 11px;'
                'font-family:%s;font-size:11.5px;color:%s;background:%s;border:1px solid %s;'
                'border-radius:4px;cursor:pointer;font-weight:%d;">%s</span>') % (
            MONO, BG if on else TXT2, ACC if on else 'transparent', ACC if on else LINE2, 600 if on else 400, esc(o))
    return '<div style="display:flex;gap:5px;flex-wrap:wrap;">%s</div>' % out

_ns_left = ('<div style="padding:14px 16px;display:flex;flex-direction:column;gap:14px;overflow:hidden;">%s%s%s%s%s</div>') % (
  field('Target URL', inp('https://shop.example.eu/pricing', ''), 'The page the builder starts from. Pagination and detail pages are discovered by the agent.'),
  field('What should it extract?', area('Every product on the pricing pages: product name, price, currency and a link to the detail page. Follow pagination to the end. Skip out-of-stock items.'), 'Plain language. The builder turns this into an output schema and a step list.'),
  field('Output schema', seg(['Let the builder infer it', 'Paste a JSON schema', 'Reuse from another scraper'], 'Let the builder infer it')),
  field('Engine', seg(['Auto', 'http', 'patchright', 'camoufox'], 'Auto'),
        'Auto starts with plain http and escalates only when the page blocks or renders empty.'),
  ('<div style="display:flex;gap:12px;">'
   '<div style="flex:1 1 0;">%s</div><div style="flex:1 1 0;">%s</div></div>') % (
      field('Login profile', inp('none', '')), field('Proxy pool', inp('none', ''))))

_ns_right = ('<div style="padding:14px 16px;display:flex;flex-direction:column;gap:14px;">%s%s%s%s</div>') % (
  field('Schedule', seg(['Manual', 'Every 15 min', 'Hourly', 'Daily', 'Cron…'], 'Hourly')),
  field('When a run fails validation', seg(['Repair, ask me', 'Repair, auto if minor', 'Repair, always auto', 'Just alert'], 'Repair, ask me')),
  field('LLM fallback while a repair is pending', seg(['On · sonnet-5', 'On · opus-5', 'Off'], 'On · sonnet-5'),
        'Delivers flagged rows so downstream consumers keep receiving data.'),
  field('Monthly agent budget', inp('$10.00', '', w='140px'), 'Builds and repairs stop when the budget is reached.'))

_ns_next = ('<div style="padding:12px;display:flex;flex-direction:column;gap:9px;">%s'
  '<div style="display:flex;flex-direction:column;gap:7px;">%s</div>'
  '<div style="border-top:1px solid %s;padding-top:9px;display:flex;justify-content:space-between;">'
  '<span style="font-family:%s;font-size:11px;color:%s;">estimated build cost</span>'
  '<span style="font-family:%s;font-size:12px;color:%s;font-weight:600;">$0.40 – $1.80</span></div></div>') % (
  '<div style="font-family:%s;font-size:11px;color:%s;line-height:1.6;">The builder opens the page in a real browser, '
  'probes selectors, writes a YAML step list, then runs it once. You review the result before it is saved.</div>' % (MONO, TXT2),
  ''.join('<div style="display:flex;gap:8px;align-items:flex-start;">'
          '<span style="width:17px;height:17px;flex-shrink:0;border-radius:50%%;border:1px solid %s;color:%s;'
          'font-family:%s;font-size:9.5px;display:flex;align-items:center;justify-content:center;">%d</span>'
          '<span style="font-family:%s;font-size:11px;color:%s;line-height:1.5;">%s</span></div>' % (
              LINE2, TXT3, MONO, i + 1, MONO, TXT2, t)
          for i, t in enumerate(['Explore the page and capture a fixture',
                                 'Propose an output schema and step list',
                                 'Test-run the script against the live page',
                                 'Show you the rows before saving'])),
  LINE, MONO, TXT3, MONO, TXT)

ns_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:400px;flex-shrink:0;display:flex;">%s</div>'
  '<div style="width:290px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div>') % (
  panel(phead('What to scrape') + _ns_left, grow=True, style='flex:1 1 0;'),
  panel(phead('How it should run') + _ns_right, grow=True, style='flex:1 1 0;'),
  panel(phead('What happens next') + _ns_next),
  panel(phead('Legal & etiquette')
        + ('<div style="padding:12px;display:flex;flex-direction:column;gap:10px;flex:1 1 0;">%s%s%s'
           '<div style="margin-top:auto;font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">%s '
           'Check the site’s terms before scheduling. Rate limits and robots are yours to set.</div></div>') % (
           kv('respect robots.txt', chip('on', OK)), kv('min delay', chip('2–6s', TXT2)),
           kv('max pages per run', chip('20', TXT2)), MONO, TXT3, icon('alert', 11, WARN)), grow=True))

add('NewScraper.dc.html', 'New scraper', 'Scrapers',
    pagehead('New scraper', 'step 1 of 2',
             btn('Cancel', 'ghost', href='Scrapers.dc.html') + btn('Start builder', 'primary', 'zap', 'BuilderLive.dc.html')),
    ns_body)

# ============================== 10. BUILDER LIVE ==============================
def turn(kind, text, tools=None):
    tools = tools or []
    if kind == 'agent':
        head = ('<div style="display:flex;align-items:center;gap:6px;">%s'
                '<span style="font-family:%s;font-size:10px;color:%s;letter-spacing:.09em;text-transform:uppercase;'
                'font-weight:600;">builder</span></div>') % (icon('zap', 11, AGENT), SANS, AGENT)
    else:
        head = ('<div style="display:flex;align-items:center;gap:6px;">%s'
                '<span style="font-family:%s;font-size:10px;color:%s;letter-spacing:.09em;text-transform:uppercase;'
                'font-weight:600;">runner</span></div>') % (icon('term', 11, TXT3), SANS, TXT3)
    tl = ''
    for t, r, ok_ in tools:
        tl += ('<div style="display:flex;align-items:center;gap:7px;padding:4px 8px;background:%s;border:1px solid %s;'
               'border-radius:3px;">'
               '<span style="color:%s;font-family:%s;font-size:9px;">%s</span>'
               '<span style="font-family:%s;font-size:10.5px;color:%s;font-weight:500;">%s</span>'
               '<span style="font-family:%s;font-size:10.5px;color:%s;flex-grow:1;overflow:hidden;'
               'text-overflow:ellipsis;white-space:nowrap;">%s</span></div>') % (
            SURF2, LINE, OK if ok_ else WARN, MONO, '●' if ok_ else '▲', MONO, TXT2, esc(t), MONO, TXT3, esc(r))
    return ('<div style="display:flex;flex-direction:column;gap:6px;padding:9px 12px;border-bottom:1px solid %s;">'
            '%s<div style="font-family:%s;font-size:11.5px;color:%s;line-height:1.55;">%s</div>%s</div>') % (
        LINE, head, MONO, TXT2 if kind == 'agent' else TXT3, esc(text),
        ('<div style="display:flex;flex-direction:column;gap:4px;">%s</div>' % tl) if tl else '')

_bl_transcript = ''.join([
    turn('agent', 'Opening the page and taking an accessibility snapshot before I guess at any selector.',
         [('navigate', '200 · 2.1s · shop.example.eu/pricing', True),
          ('snapshot', 'a11y tree 412 nodes · html 612 KB trimmed to 38 KB', True)]),
    turn('agent', 'The grid repeats an element with class product-card, 42 on this page. Probing candidate field selectors inside it.',
         [('extract_probe', 'css=.product-card → 42 matched', True),
          ('extract_probe', 'h3.title → 42/42 non-empty', True),
          ('extract_probe', '[data-testid=price-now] → 42/42 non-empty', True),
          ('extract_probe', '.price → 14/42 non-empty', False)]),
    turn('agent', 'The .price node is empty until a script fills it, so I am taking the data-testid attribute instead. It is stable and present at first paint.',
         [('extract_probe', 'a.detail[href] → 42/42 absolute', True),
          ('extract_probe', 'nav a[rel=next] → present, 3 pages', True)]),
    turn('agent', 'Writing the step list with pagination capped at 20 pages, and a validation block derived from what I measured: 118 rows across 3 pages, no nulls on price or url.',
         [('propose_script', 'v1 · 52 lines · schema 4 fields', True)]),
    turn('runner', 'Test run against the live page.',
         [('run_script', '118 rows · 29s · all 6 rules passed', True)]),
    turn('agent', 'Done. 118 rows, price and url complete on every row. Saving the fixture so a future repair can be tested offline first.', []),
])

_bl_script = """version: 1
engine: browser
stealth: patchright
steps:
  - op: goto
    url: "https://shop.example.eu/pricing?page={{page}}"
  - op: wait_for
    selector: "css=.product-card"
    timeout_ms: 15000
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields:
      name:
        selector: "h3.title"
      price:
        selector: "[data-testid=price-now]"
        parse: money
      url:
        selector: "a.detail"
        absolute: true"""

def prog(steps, active):
    out = ''
    for i, s in enumerate(steps):
        done = i < active; on = i == active
        c = OK if done else (ACC if on else TXT3)
        g = '●' if done else ('◐' if on else '○')
        out += ('<div style="display:flex;align-items:center;gap:6px;">'
                '<span style="color:%s;font-family:%s;font-size:10px;">%s</span>'
                '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:%d;">%s</span></div>') % (
            c, MONO, g, MONO, TXT if (done or on) else TXT3, 600 if on else 400, esc(s))
        if i < len(steps) - 1:
            out += '<span style="width:16px;height:1px;background:%s;"></span>' % (OK if done else LINE2)
    return '<div style="display:flex;align-items:center;gap:6px;">%s</div>' % out

bl_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="flex:1.15 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:330px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="width:360px;flex-shrink:0;display:flex;">%s</div></div>') % (
  panel(phead('Agent transcript', chip('opus-5', AGENT), '6 turns · 21 tool calls')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % _bl_transcript, grow=True, style='flex:1 1 0;'),
  panel(phead('Browser', chip('live', ACC), 'headed · 1280×800')
        + ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:9px;">%s'
           '<div style="font-family:%s;font-size:10.5px;color:%s;">shop.example.eu/pricing?page=3</div></div>') % (
           fake_page(w=304, h=184), MONO, TXT3)),
  panel(phead('Spend')
        + ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:9px;flex:1 1 0;">%s%s%s%s'
           '<div style="border-top:1px solid %s;padding-top:9px;display:flex;justify-content:space-between;">'
           '<span style="font-family:%s;font-size:11px;color:%s;">this build</span>'
           '<span style="font-family:%s;font-size:13px;color:%s;font-weight:600;">$0.71</span></div>'
           '<div style="margin-top:auto;font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">'
           'Budget $10.00 per month. Snapshots are trimmed to the accessibility tree plus visible markup, '
           'which is what keeps a build under a dollar.</div></div>') % (
           kv('input tokens', mono('184,220', 11.5)), kv('output tokens', mono('9,415', 11.5)),
           kv('cache read', mono('96,400', 11.5, OK)), kv('tool calls', mono('21', 11.5)),
           LINE, MONO, TXT3, MONO, AGENT, MONO, TXT3), grow=True),
  panel(phead('Candidate script', chip('v1 draft', AGENT), 'testing')
        + code_block(_bl_script)
        + ('<div style="flex-shrink:0;border-top:1px solid %s;background:%s;padding:9px 12px;display:flex;'
           'align-items:center;gap:8px;">%s<span style="flex-grow:1;"></span>%s%s</div>') % (
           LINE, SURF2, status('ok', '118 rows · 6 of 6 rules passed'),
           btn('Discard', 'ghost'), btn('Save scraper', 'primary', 'check', 'ScraperDetail.dc.html')),
        grow=True, style='flex:1 1 0;'))

add('BuilderLive.dc.html', 'Builder live', 'Scrapers',
    pagehead('Building shop-eu-prices', '',
             prog(['Explore', 'Propose', 'Test', 'Save'], 2) + '<span style="width:12px;"></span>'
             + btn('Stop', 'danger', 'stop')), bl_body)

# ============================== 11. REPAIRS ==============================
_rep_rows = []
for st, scr, frm, to, reason, tested, policy, when in [
    ('drift','shop-eu-prices','v7','v8','price selector returned empty on 28 of 42 cards','118 rows · 6/6 passed','auto_if_minor','2m ago'),
    ('drift','competitor-skus','v5','v6','pagination stopped at page 3, site added a load-more button','912 rows · 6/6 passed','manual','18m ago')]:
    _rep_rows.append([
        status(st, 'pending'),
        alink(scr, 'ScraperDetail.dc.html', TXT, 11.5),
        chip(frm, TXT3) + '<span style="color:%s;padding:0 4px;">→</span>' % TXT3 + chip(to, AGENT),
        '<span style="font-family:%s;font-size:11.5px;color:%s;">%s</span>' % (MONO, TXT2, esc(reason)),
        status('ok', tested), chip(policy, WARN if policy == 'manual' else TXT3), mono(when, 11.5, TXT3),
        '<div style="display:flex;gap:5px;justify-content:flex-end;">%s%s</div>' % (
            btn('Review', 'solid', href='RepairReview.dc.html', small=True), iconbtn('x', 'Reject candidate', c=FAIL))])

_rep_hist = []
for st, scr, frm, to, what, who, when in [
    ('ok','vendor-stock','v3','v4','wait_for timeout raised to 15s','auto · minor','14 sep'),
    ('ok','example-products','v2','v3','title selector moved to h3.name','you','12 sep'),
    ('fail','news-archive','v1','v2','candidate rejected, blocked before extraction','you','11 sep'),
    ('ok','gov-tenders','v1','v1','no change needed, transient 503','auto','9 sep'),
    ('ok','hn-frontpage','v1','v1','no change needed, empty page once','auto','7 sep'),
    ('ok','shop-eu-prices','v6','v7','pagination guard added','you','3 sep')]:
    _rep_hist.append([status(st, ''), alink(scr, 'ScraperDetail.dc.html', TXT2, 11.5),
                      chip(frm, TXT3) + '<span style="color:%s;padding:0 3px;">→</span>' % TXT3 + chip(to, TXT3),
                      '<span style="font-family:%s;font-size:11.5px;color:%s;">%s</span>' % (MONO, TXT3, esc(what)),
                      chip(who, AGENT if 'auto' in who else TXT3), mono(when, 11.5, TXT3)])

rep_body = ('<div style="%s">'
  '<div style="display:flex;gap:12px;flex-shrink:0;">%s%s%s%s</div>%s%s</div>') % (
  PAD,
  tile('pending approval', '2', 'both tested and passing', WARN, ic='branch'),
  tile('auto-promoted 30d', '7', 'all minor selector fixes', ic='check'),
  tile('rejected 30d', '1', 'news-archive v2', ic='x'),
  tile('repair spend 30d', '$6.84', '11 repair runs · opus-5', AGENT, ic='zap'),
  panel(phead('Pending candidates', btn('Approve all minor', 'ghost', 'check', small=True), '2 awaiting you')
        + '<div style="overflow:hidden;">%s</div>' % table(
            [('','78px','left'),('Scraper','170px','left'),('Version','120px','left'),('Why','470px','left'),
             ('Test run','200px','left'),('Policy','120px','left'),('Proposed','80px','left'),('','126px','right')],
            _rep_rows)),
  panel(phead('History', '', 'last 30 days')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % table(
            [('','34px','left'),('Scraper','170px','left'),('Version','110px','left'),('Change','620px','left'),
             ('Approved by','120px','left'),('When','90px','left')], _rep_hist), grow=True))

add('Repairs.dc.html', 'Repairs', 'Repairs', pagehead('Repairs', '', ''), rep_body)

# ============================== 12. REPAIR REVIEW ==============================
DIFF = [
 ('h', '', '', '@@ scrapers/shop-eu-prices.yaml · v7 → v8 @@'),
 (' ', '2', '2', 'version: 7'),
 ('-', '2', '',  'version: 7'),
 ('+', '',  '2', 'version: 8'),
 (' ', '3', '3', 'engine: browser'),
 (' ', '4', '4', 'stealth: patchright'),
 (' ', '', '',  ''),
 ('h', '', '', '@@ steps[2].fields.price @@'),
 (' ','21','21','      price:'),
 ('-','22','',  '        selector: ".price"'),
 ('+','',  '22','        selector: "[data-testid=price-now]"'),
 ('+','',  '23','        fallback_selectors: [".price", ".product-card .amount"]'),
 (' ','23','24','        attr: text'),
 (' ','24','25','        parse: money'),
 (' ', '', '',  ''),
 ('h', '', '', '@@ steps[2].fields.currency @@'),
 (' ','26','27','      currency:'),
 ('-','27','',  '        selector: ".price [data-cur]"'),
 ('+','',  '28','        selector: "[data-testid=price-now] [data-cur]"'),
 (' ','28','29','        attr: text'),
]
def diff_block(rows):
    out = ''
    for kind, ln_o, ln_n, txt in rows:
        if kind == 'h':
            out += ('<div style="padding:5px 12px;background:%s;border-top:1px solid %s;border-bottom:1px solid %s;'
                    'font-family:%s;font-size:10.5px;color:%s;">%s</div>') % (SURF2, LINE, LINE, MONO, ACC, esc(txt))
            continue
        bg = 'transparent'; mark = ' '; mc = TXT3
        if kind == '-': bg = '#2A1416'; mark = '−'; mc = FAIL
        if kind == '+': bg = '#0F2418'; mark = '+'; mc = OK
        body = _sy(txt) if txt.strip() else '&nbsp;'
        out += ('<div style="display:flex;background:%s;min-height:18px;">'
                '<span style="width:34px;flex-shrink:0;text-align:right;padding-right:7px;color:%s;font-family:%s;'
                'font-size:10.5px;line-height:18px;font-variant-numeric:tabular-nums;">%s</span>'
                '<span style="width:34px;flex-shrink:0;text-align:right;padding-right:7px;color:%s;font-family:%s;'
                'font-size:10.5px;line-height:18px;font-variant-numeric:tabular-nums;">%s</span>'
                '<span style="width:15px;flex-shrink:0;text-align:center;color:%s;font-family:%s;font-size:11px;'
                'line-height:18px;">%s</span>'
                '<span style="font-family:%s;font-size:11.5px;line-height:18px;white-space:pre;">%s</span></div>') % (
            bg, TXT3, MONO, esc(ln_o), TXT3, MONO, esc(ln_n), mc, MONO, mark, MONO, body)
    return '<div style="flex:1 1 0;overflow:hidden;padding:6px 0;background:%s;">%s</div>' % (BG, out)

_rr_valid = ''
for r, before, after in [('max_null_rate.price','0.66 ✕','0.00 ●'),
                         ('row_count_band','118 ✕','452 ●'),
                         ('min_rows','118 ●','452 ●'),
                         ('max_rows','118 ●','452 ●'),
                         ('max_null_rate.url','0.00 ●','0.00 ●'),
                         ('unique.url','118 ●','452 ●')]:
    bc = FAIL if '✕' in before else TXT3
    ac = OK
    _rr_valid += ('<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid %s;">'
      '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;">%s</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;width:74px;text-align:right;">%s</span>'
      '<span style="color:%s;padding:0 3px;">→</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;width:74px;text-align:right;font-weight:500;">%s</span></div>') % (
      LINE, MONO, TXT2, esc(r), MONO, bc, esc(before), TXT3, MONO, ac, esc(after))

_rr_why = ('<div style="padding:12px;display:flex;flex-direction:column;gap:10px;flex:1 1 0;overflow:hidden;">'
  '<div style="display:flex;align-items:center;gap:6px;">%s<span style="font-family:%s;font-size:10px;color:%s;'
  'letter-spacing:.09em;text-transform:uppercase;font-weight:600;">repair agent · opus-5</span></div>'
  '<div style="font-family:%s;font-size:11.5px;color:%s;line-height:1.6;">%s</div>'
  '<div style="display:flex;flex-direction:column;gap:5px;">%s</div></div>') % (
  icon('zap', 12, AGENT), SANS, AGENT, MONO, TXT2,
  esc('The site now renders the price into a data-testid node and leaves the old .price element empty until a '
      'client script fills it. The run extracts before that script finishes, so price came back null on 28 of 42 '
      'cards. I switched the selector to the attribute that is present at first paint and kept the two old '
      'selectors as fallbacks, so a rollback of their change does not break this again. The currency selector was '
      'nested inside .price and had to move with it. No steps were added or removed.'),
  ''.join(kv(k, v) for k, v in [
      ('classified', chip('minor · selectors only', OK)),
      ('steps changed', mono('0', 11.5)),
      ('fields changed', mono('2 of 4', 11.5)),
      ('tested on', chip('live page · 12:04:41', TXT3)),
      ('fixture replay', status('ok', 'passed')),
      ('repair cost', mono('$0.22', 11.5, AGENT))]))

rr_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:392px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s%s</div></div>') % (
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:8px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">Unified diff</span>%s%s'
         '<span style="flex-grow:1;"></span>%s%s</div>') % (LINE, SURF2, SANS, TXT2,
        chip('+3 −3', TXT2), chip('3 hunks', TXT3), btn('Side by side', 'ghost', small=True),
        iconbtn('code', 'Open full file', 'Script.dc.html'))
        + diff_block(DIFF)
        + ('<div style="flex-shrink:0;border-top:1px solid %s;background:%s;padding:10px 12px;display:flex;'
           'align-items:center;gap:9px;">%s<span style="flex-grow:1;"></span>%s%s%s</div>') % (
           LINE, SURF2, status('ok', 'test run passed · 452 rows · 6 of 6 rules'),
           btn('Reject', 'danger', 'x'), btn('Test again', 'ghost', 'refresh'),
           btn('Approve & promote to active', 'primary', 'check', 'ScraperDetail.dc.html')),
        grow=True, style='flex:1 1 0;'),
  panel(phead('Why', '', 'candidate v8') + _rr_why, grow=True),
  panel(phead('Validation, before and after', chip('2 fixed', OK))
        + '<div style="padding:4px 12px 10px;">%s</div>' % _rr_valid),
  panel(phead('Sample rows from the test run', '', 'first 5 of 452')
        + '<div style="overflow:hidden;">%s</div>' % table(
            [('name','210px','left'),('price','70px','right'),('cur','48px','left')],
            [['<span style="font-family:%s;font-size:11px;color:%s;">%s</span>' % (MONO, TXT, esc(n)),
              mono(p, 11.5, OK), mono(c, 11, TXT3)]
             for n, p, c in [('Lavazza Qualita Rossa 1kg','8.95','EUR'), ('Illy Classico Beans 1kg','18.40','EUR'),
                             ('Segafredo Intermezzo 1kg','12.49','EUR'), ('Kimbo Napoletano 1kg','11.20','EUR'),
                             ('Pellini Top 1kg','13.75','EUR')]])))

add('RepairReview.dc.html', 'Repair review', 'Repairs',
    pagehead('shop-eu-prices  v7 → v8', 'proposed 2 minutes ago after run #4182',
             chip('auto_if_minor', WARN) + btn('Open run #4182', 'ghost', 'ext', 'RunDetail.dc.html')),
    rr_body)

# ============================== 13. NETWORK ==============================
_prof_rows = []
for st, name, dom, kind, exp, used in [
    ('ok','shop-eu-login','shop.example.eu','storage_state + totp','in 22 days','shop-eu-prices'),
    ('ok','vendor-portal','vendors.example.com','persistent context','no expiry','vendor-stock'),
    ('drift','jobs-sso','jobs.example.eu','storage_state','in 3 days','job-board-eu'),
    ('fail','news-paywall','news.example.org','storage_state','expired 2d ago','news-archive')]:
    _prof_rows.append([status(st), alink(name, '#', TXT, 11.5), mono(dom, 11, TXT2), chip(kind, TXT3),
                       mono(exp, 11.5, FAIL if 'expired' in exp else (WARN if '3 days' in exp else TXT2)),
                       mono(used, 11, TXT3),
                       '<div style="display:flex;gap:5px;justify-content:flex-end;">%s%s</div>' % (
                           btn('Re-login', 'ghost', 'key', small=True), iconbtn('trash', 'Delete profile', c=FAIL))])

_pool_rows = []
for st, name, vendor, kind, gb, cost, succ, rot in [
    ('ok','eu-resi','Decodo','residential','4.2 GB','$16.80','94%','sticky per run'),
    ('ok','us-resi','Evomi','residential','1.1 GB','$1.09','91%','round robin'),
    ('drift','dc-cheap','Webshare','datacenter','0.4 GB','$0.90','38%','round robin'),
    ('paused','eu-mobile','Evomi','mobile','0.0 GB','$0.00','—','sticky per run')]:
    _pool_rows.append([status(st), alink(name, '#', TXT, 11.5), mono(vendor, 11, TXT2), chip(kind, TXT3),
                       mono(gb, 11.5), mono(cost, 11.5, TXT2),
                       mono(succ, 11.5, FAIL if succ == '38%' else (OK if succ != '—' else TXT3)),
                       chip(rot, TXT3),
                       '<div style="display:flex;gap:5px;justify-content:flex-end;">%s%s</div>' % (
                           btn('Test', 'ghost', 'zap', small=True), iconbtn('gear', 'Pool settings'))])

_ladder = ''
for i, (name, ok_, tot, note) in enumerate([
    ('http', 612, 640, 'curl_cffi · chrome 152 profile'),
    ('patchright', 118, 132, 'chromium 153 · persistent context'),
    ('patchright + proxy', 21, 28, 'eu-resi · sticky'),
    ('camoufox', 3, 9, 'firefox 152 · fingerprint spoof'),
    ('byparr', 1, 6, 'sidecar · cf challenge solver')]):
    pct = int(ok_ / tot * 100)
    c = OK if pct >= 85 else (WARN if pct >= 50 else FAIL)
    _ladder += ('<div style="display:flex;flex-direction:column;gap:5px;padding:8px 0;border-bottom:1px solid %s;">'
      '<div style="display:flex;align-items:center;gap:8px;">'
      '<span style="width:15px;height:15px;border-radius:3px;background:%s;color:%s;font-family:%s;font-size:9.5px;'
      'display:flex;align-items:center;justify-content:center;flex-shrink:0;">%d</span>'
      '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:500;flex-grow:1;">%s</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;font-variant-numeric:tabular-nums;">%d of %d</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;width:34px;text-align:right;font-weight:600;">%d%%</span></div>'
      '<div style="height:4px;background:%s;border-radius:2px;overflow:hidden;margin-left:23px;">'
      '<div style="width:%d%%;height:100%%;background:%s;"></div></div>'
      '<span style="font-family:%s;font-size:10px;color:%s;margin-left:23px;">%s</span></div>') % (
      LINE, SURF3, TXT3, MONO, i + 1, MONO, TXT, esc(name), MONO, TXT3, ok_, tot, MONO, c, pct,
      SURF2, pct, c, MONO, TXT3, esc(note))

net_body = ('<div style="%s">'
  '<div style="display:flex;gap:12px;flex:1 1 0;min-height:0;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="width:330px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div></div>') % (
  PAD,
  panel(phead('Login profiles', btn('New profile', 'ghost', 'plus', small=True), '4 · 1 expired')
        + '<div style="overflow:hidden;">%s</div>' % table(
            [('','34px','left'),('Profile','150px','left'),('Domain','190px','left'),('Kind','160px','left'),
             ('Session','120px','left'),('Used by','160px','left'),('','130px','right')], _prof_rows)
        + ('<div style="border-top:1px solid %s;background:%s;padding:9px 12px;font-family:%s;font-size:10.5px;'
           'color:%s;line-height:1.5;">%s A re-login opens a headed browser on this machine so you can sign in by '
           'hand. The session is then saved as storage_state with IndexedDB included. TOTP codes come from the '
           'stored secret.</div>') % (LINE, SURF2, MONO, TXT3, icon('key', 11, TXT3))),
  panel(phead('Proxy pools', btn('Add pool', 'ghost', 'plus', small=True), '4 · 5.7 GB this month')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % table(
            [('','34px','left'),('Pool','120px','left'),('Vendor','110px','left'),('Type','120px','left'),
             ('Used','80px','right'),('Cost','80px','right'),('Success','76px','right'),('Rotation','140px','left'),
             ('','120px','right')], _pool_rows)
        + ('<div style="margin-top:auto;border-top:1px solid %s;background:%s;padding:9px 12px;font-family:%s;'
           'font-size:10.5px;color:%s;line-height:1.5;">%s dc-cheap is at 38%%. Datacenter ranges are usually '
           'blocked before a challenge is even served, so it is only worth keeping for sites that never '
           'challenge.</div>') % (LINE, SURF2, MONO, TXT3, icon('alert', 11, WARN)), grow=True),
  panel(phead('Escalation ladder', btn('By scraper', 'ghost', 'chev', 'Coverage.dc.html', small=True), 'all · 7 days')
        + '<div style="padding:4px 12px 10px;">%s</div>' % _ladder),
  panel(phead('Solvers & captcha')
        + ('<div style="padding:10px 12px;display:flex;flex-direction:column;gap:2px;flex:1 1 0;">%s%s%s%s%s'
           '<div style="margin-top:auto;padding-top:10px;font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">'
           '%s Byparr and FlareSolverr both have open reports of a solved challenge returning cookies that the '
           'next request rejects. Treat intermittent failures here as expected.</div></div>') % (
           kv('byparr sidecar', status('ok', 'up · :8191')),
           kv('solve rate 7d', mono('64%', 11.5, WARN) + ' ' + mono('9 of 14', 11, TXT3)),
           kv('capmonster', status('ok', '$4.12 left')),
           kv('turnstile solved', mono('6', 11.5) + ' ' + mono('$0.008', 11, TXT3)),
           kv('recaptcha v2', mono('0', 11.5, TXT3)),
           MONO, TXT3, icon('alert', 11, WARN)), grow=True))

add('Network.dc.html', 'Network', 'Network',
    pagehead('Network', 'profiles, proxies and solvers', btn('Test all pools', 'ghost', 'zap') + btn('New profile', 'primary', 'plus')),
    net_body)

# ============================== 14. DELIVERY ==============================
_dt_rows = []
for st, typ, dest, scr, fmt, last, retries in [
    ('ok','webhook','https://ops.internal/hooks/prices','shop-eu-prices','json rows','12:04:18','0'),
    ('ok','s3','s3://scrape-archive/shop-eu/','shop-eu-prices','parquet','12:04:18','0'),
    ('fail','mcp','sheets-server · append_rows','shop-eu-prices','json rows','12:04:19','2 of 5'),
    ('ok','webhook','https://ops.internal/hooks/stock','vendor-stock','json rows','11:45:00','0'),
    ('ok','s3','s3://scrape-archive/vendor/','vendor-stock','parquet','11:45:00','0'),
    ('ok','file','./data/exports/hn/','hn-frontpage','jsonl','12:04:09','0'),
    ('ok','apprise','telegram · ops-alerts','all scrapers','failures only','11:52:03','0'),
    ('ok','mcp','notion-server · create_rows','gov-tenders','json rows','11:58:12','0'),
    ('paused','email','ops@example.com','competitor-skus','csv attachment','—','—')]:
    _dt_rows.append([status(st), chip(typ, ACC if typ == 'mcp' else TXT3),
                     mono(dest, 11, TXT2), alink(scr, 'ScraperDetail.dc.html', TXT2, 11),
                     chip(fmt, TXT3), mono(last, 11.5, TXT3),
                     mono(retries, 11.5, FAIL if retries not in ('0', '—') else TXT3),
                     '<div style="display:flex;gap:5px;justify-content:flex-end;">%s%s</div>' % (
                         btn('Test', 'ghost', 'send', small=True),
                     iconbtn('sliders', 'Field mapping', 'McpConsole.dc.html') if typ == 'mcp'
                     else iconbtn('gear', 'Target settings'))])

_dlog = ''.join([
    logline('12:04:19','err','mcp','sheets-server · append_rows · timeout after 30s · retry 2 of 5 in 4m'),
    logline('12:04:18','ok','s3','shop-eu/4182.parquet · 41 KB · 118 rows · 620ms'),
    logline('12:04:18','ok','webhook','ops.internal/hooks/prices · 202 · 118 rows · 210ms'),
    logline('12:04:09','ok','file','./data/exports/hn/4186.jsonl · 30 rows'),
    logline('11:58:12','ok','mcp','notion-server · create_rows · 88 rows · 1.9s'),
    logline('11:52:03','ok','apprise','telegram ops-alerts · news-archive blocked'),
    logline('11:45:00','ok','s3','vendor/4178.parquet · 388 KB · 1,203 rows · 2.1s'),
    logline('11:45:00','ok','webhook','ops.internal/hooks/stock · 202 · 1,203 rows · 840ms'),
    logline('11:41:22','ok','file','./data/exports/hn/4177.jsonl · 30 rows'),
    logline('11:30:07','ok','mcp','notion-server · create_rows · 88 rows · 1.7s'),
    logline('11:26:18','ok','s3','vendor/4171.parquet · 384 KB · 1,189 rows · 2.0s'),
    logline('11:22:55','ok','file','./data/exports/hn/4170.jsonl · 30 rows'),
])

_mcp_tools = ''.join(
    '<div style="display:flex;align-items:baseline;gap:8px;padding:4px 0;border-bottom:1px solid %s;">'
    '<span style="font-family:%s;font-size:11px;color:%s;width:130px;flex-shrink:0;">%s</span>'
    '<span style="font-family:%s;font-size:10.5px;color:%s;flex-grow:1;">%s</span></div>' % (
        LINE, MONO, ACC, esc(n), MONO, TXT3, esc(d))
    for n, d in [('list_scrapers','name, status, schedule'), ('get_scraper','full config and active script'),
                 ('create_scraper','url + goal → runs the builder'), ('run_scraper','trigger, optionally wait'),
                 ('get_run','status, metrics, validator report'), ('get_results','rows by scraper or run'),
                 ('search_results','query across stored records'), ('get_pending_repairs','candidates awaiting approval'),
                 ('approve_repair','promote a candidate to active')])

del_body = ('<div style="%s">'
  '<div style="display:flex;gap:12px;flex:1 1 0;min-height:0;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="width:340px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div></div>') % (
  PAD,
  panel(phead('Targets', btn('Add target', 'ghost', 'plus', small=True), '9 · 1 retrying')
        + '<div style="overflow:hidden;">%s</div>' % table(
            [('','34px','left'),('Type','90px','left'),('Destination','300px','left'),('Scraper','150px','left'),
             ('Format','130px','left'),('Last','80px','left'),('Retries','70px','right'),('','120px','right')],
            _dt_rows)),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:8px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">Delivery log</span>%s%s'
         '<span style="flex-grow:1;"></span>%s</div>') % (LINE, SURF2, SANS, TXT2,
        chip('ok 11', OK), chip('failed 1', FAIL), iconbtn('refresh', 'Refresh delivery log'))
        + '<div style="flex:1 1 0;overflow:hidden;padding:7px 0;background:%s;">%s</div>' % (BG, _dlog),
        grow=True, style='flex:1 1 0;'),
  panel(phead('MCP server', status('ok', 'listening'), 'streamable http')
        + ('<div style="padding:10px 12px;display:flex;flex-direction:column;gap:9px;">'
           '<div style="display:flex;align-items:center;gap:7px;padding:7px 9px;background:%s;border:1px solid %s;'
           'border-radius:4px;">'
           '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;overflow:hidden;text-overflow:ellipsis;'
           'white-space:nowrap;">http://127.0.0.1:8088/mcp</span>%s</div>%s%s%s</div>') % (
           SURF2, LINE, MONO, TXT, iconbtn('copy', 'Copy MCP endpoint'),
           kv('auth', chip('bearer · rotated 3d ago', TXT2)),
           kv('stdio entry', mono('smartscraper mcp --stdio', 10.5, TXT2)),
           kv('clients 24h', mono('2', 11.5) + ' ' + chip('claude code', TXT3)))),
  panel(phead('Tools exposed', btn('Console', 'ghost', 'term', 'McpConsole.dc.html', small=True), '9')
        + '<div style="padding:5px 12px 10px;flex:1 1 0;overflow:hidden;">%s</div>' % _mcp_tools, grow=True))

add('Delivery.dc.html', 'Delivery', 'Delivery',
    pagehead('Delivery', 'targets, log and the MCP server', btn('Retry failed', 'ghost', 'refresh') + btn('Add target', 'primary', 'plus')),
    del_body)

# ============================== 15. SETTINGS ==============================
def toggle(on=True, lab=''):
    return ('<span style="display:inline-flex;align-items:center;gap:7px;">'
            '<span style="width:28px;height:16px;border-radius:8px;background:%s;position:relative;display:inline-block;">'
            '<span style="position:absolute;top:2px;%s:2px;width:12px;height:12px;border-radius:50%%;background:%s;"></span>'
            '</span><span style="font-family:%s;font-size:11px;color:%s;">%s</span></span>') % (
        ACC if on else SURF3, 'right' if on else 'left', BG if on else TXT3, MONO, TXT2 if on else TXT3, esc(lab))

def sect(title, rows, foot=''):
    f = ('<div style="border-top:1px solid %s;margin-top:4px;padding-top:9px;font-family:%s;font-size:10.5px;'
         'color:%s;line-height:1.5;">%s</div>') % (LINE, MONO, TXT3, foot) if foot else ''
    return panel(phead(title) + '<div style="padding:5px 12px 11px;">%s%s</div>' % (''.join(rows), f))

set_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s%s</div></div>') % (
  sect('Models & agents', [
      kv('builder', chip('claude-opus-5', AGENT) + ' ' + chip('effort high', TXT3)),
      kv('repair', chip('claude-opus-5', AGENT) + ' ' + chip('effort high', TXT3)),
      kv('fallback extract', chip('claude-sonnet-5', ACC) + ' ' + chip('structured', TXT3)),
      kv('harness', chip('claude agent sdk', TXT2)),
      kv('prompt caching', toggle(True, 'on · system + tools')),
      kv('max turns per build', mono('40', 11.5)),
      kv('snapshot budget', mono('40 KB per turn', 11.5)),
  ], 'Builder and repair share one gateway, so swapping the harness for the direct API later does not touch the agents.'),
  sect('Budgets', [
      kv('monthly cap', mono('$60.00', 11.5)),
      kv('spent this month', mono('$18.42', 11.5, OK) + ' ' + mono('31%', 11, TXT3)),
      kv('per-scraper default', mono('$10.00', 11.5)),
      kv('stop at cap', toggle(True, 'builds and repairs halt')),
      kv('alert at', mono('80%', 11.5) + ' ' + chip('telegram', TXT3)),
  ]),
  sect('Engines & etiquette', [
      kv('default engine', chip('auto · escalate', TXT2)),
      kv('escalation ladder', toggle(True, '5 rungs')),
      kv('headed browsers', toggle(True, 'less detectable')),
      kv('respect robots.txt', toggle(True, 'per scraper override')),
      kv('global rate limit', mono('2–6s per request', 11.5)),
      kv('max concurrent runs', mono('3', 11.5)),
      kv('run timeout', mono('10 min hard kill', 11.5)),
      kv('custom_python steps', toggle(True, 'always human-approved')),
      kv('scrapers using it', mono('2', 11.5, AGENT) + ' ' + chip('see audit log', TXT3)),
  ], 'A run is a subprocess with its own timeout, so a hung browser cannot take the worker down.'),
  sect('Solvers', [
      kv('byparr sidecar', inp('http://127.0.0.1:8191', w='150px')),
      kv('captcha vendor', chip('capmonster cloud', TXT2)),
      kv('auto-solve turnstile', toggle(True, 'max $0.05 per run')),
      kv('auto-solve recaptcha', toggle(False, 'ask first')),
  ]),
  sect('Storage & retention', [
      kv('database', mono('./data/smartscraper.db', 10.5, TXT2)),
      kv('size', mono('412 MB', 11.5)),
      kv('records', mono('184,204 rows', 11.5)),
      kv('keep artifacts', mono('30 days', 11.5)),
      kv('keep traces', mono('failures only · 7 days', 11.5)),
      kv('parquet export', toggle(True, 'per run to ./data/parquet')),
      kv('nightly backup', toggle(True, '03:00 · last ok 6h ago')),
  ]),
  sect('Secrets', [
      kv('anthropic api key', status('ok', 'set · sk-ant-…f2a1')),
      kv('capmonster key', status('ok', 'set')),
      kv('encryption key', status('ok', 'from env · SS_SECRET_KEY')),
      kv('proxy credentials', mono('4 stored', 11.5, TXT2)),
      kv('totp secrets', mono('1 stored', 11.5, TXT2)),
      kv('audit log', chip('1,842 entries', ACC)),
  ], 'Secrets are encrypted at rest in SQLite with a key from the environment. This is single-user grade, not a vault.'),
  sect('Notifications', [
      kv('on run failure', toggle(True, 'telegram · ops-alerts')),
      kv('on repair pending', toggle(True, 'telegram')),
      kv('on budget 80%', toggle(True, 'telegram')),
      kv('on delivery failure', toggle(False, '')),
      kv('daily digest', toggle(True, '08:00 · email')),
  ]))

add('Settings.dc.html', 'Settings', 'Settings',
    pagehead('Settings', 'smartscraper 0.1.0 · python 3.12 · sqlite',
             btn('Discard', 'ghost') + btn('Save changes', 'primary', 'check')), set_body)

# ============================== 16. STYLE GUIDE ==============================
def swatch(name, hexv, note='', dark=True):
    return ('<div style="display:flex;align-items:center;gap:8px;padding:4px 0;">'
            '<span style="width:24px;height:24px;border-radius:3px;background:%s;border:1px solid %s;flex-shrink:0;"></span>'
            '<div style="display:flex;flex-direction:column;gap:1px;min-width:0;flex-grow:1;">'
            '<span style="font-family:%s;font-size:10.5px;color:%s;">%s</span>'
            '<span style="font-family:%s;font-size:10px;color:%s;">%s</span></div>'
            '<span style="font-family:%s;font-size:10px;color:%s;">%s</span></div>') % (
        hexv, LINE2 if dark else '#D8DCE4', MONO, TXT if dark else '#11151F', esc(name),
        MONO, TXT3, esc(hexv), MONO, TXT3, esc(note))

DARK_TOKENS = [('bg', BG, 'page'), ('surface', SURF, 'panels'), ('surface-2', SURF2, 'headers'),
               ('surface-3', SURF3, 'hover'), ('line', LINE, 'hairline'), ('line-2', LINE2, 'inputs'),
               ('text', TXT, 'primary'), ('text-2', TXT2, 'secondary'), ('text-3', TXT3, 'meta')]
ACC_TOKENS = [('accent', ACC, 'action / running'), ('ok', OK, 'passed'), ('warn', WARN, 'drift'),
              ('fail', FAIL, 'failed'), ('agent', AGENT, 'llm authored')]
LIGHT_TOKENS = [('bg', '#F7F8FA', 'page'), ('surface', '#FFFFFF', 'panels'), ('surface-2', '#F2F4F7', 'headers'),
                ('line', '#E3E7ED', 'hairline'), ('text', '#11151F', 'primary'), ('text-2', '#5A6577', 'secondary'),
                ('accent', '#0C6F6E', 'action'), ('ok', '#177332', 'passed'), ('warn', '#8F6000', 'drift'),
                ('fail', '#CF222E', 'failed'), ('agent', '#6639BA', 'llm authored')]

_type_rows = ''
for lab, size, fam, weight, use in [
    ('Page title', 17, SANS, 600, 'one per screen'), ('Panel header', 11.5, SANS, 600, 'uppercase tracking on labels'),
    ('Body', 12.5, SANS, 400, 'chrome, forms, prose'), ('Table cell', 12, MONO, 400, 'tabular numerals'),
    ('Log line', 11, MONO, 400, 'wraps, never truncates'), ('Meta', 10.5, MONO, 400, 'timestamps, counts')]:
    _type_rows += ('<div style="display:flex;align-items:baseline;gap:12px;padding:6px 0;border-bottom:1px solid %s;">'
      '<span style="font-family:%s;font-size:%spx;font-weight:%d;color:%s;flex-grow:1;">%s</span>'
      '<span style="font-family:%s;font-size:10px;color:%s;">%spx</span>'
      '<span style="font-family:%s;font-size:10px;color:%s;width:150px;text-align:right;">%s</span></div>') % (
      LINE, fam, size, weight, TXT, esc(lab), MONO, TXT3, size, MONO, TXT3, esc(use))

sg_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;overflow:hidden;">'
  '<div style="width:250px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="width:250px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="width:320px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div>') % (
  panel(phead('Palette · dark', chip('default', ACC))
        + '<div style="padding:6px 12px 10px;">%s</div>' % ''.join(swatch(*t) for t in DARK_TOKENS)),
  panel(phead('Semantic')
        + '<div style="padding:6px 12px 10px;">%s</div>' % ''.join(swatch(*t) for t in ACC_TOKENS), grow=True),
  panel(phead('Palette · light')
        + '<div style="padding:6px 12px 10px;background:#FFFFFF;flex:1 1 0;">%s</div>' % ''.join(
            swatch(n, h, no, dark=False) for n, h, no in LIGHT_TOKENS), grow=True),
  panel(phead('Radius & spacing')
        + ('<div style="padding:10px 12px;display:flex;flex-direction:column;gap:8px;">'
           '<div style="display:flex;gap:7px;align-items:flex-end;">%s</div>%s</div>') % (
           ''.join('<div style="display:flex;flex-direction:column;align-items:center;gap:4px;">'
                   '<div style="width:%dpx;height:%dpx;background:%s;border:1px solid %s;border-radius:%s;"></div>'
                   '<span style="font-family:%s;font-size:9.5px;color:%s;">%s</span></div>' % (
                       s, s, SURF2, LINE2, '3px' if i == 0 else '4px', MONO, TXT3, str(s))
                   for i, s in enumerate([4, 8, 12, 16, 24, 32])),
           '<div style="font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">4px radius everywhere. '
           '1px hairlines. No shadows, no gradients.</div>' % (MONO, TXT3))),
  panel(phead('Type scale', '', 'IBM Plex Sans + Mono')
        + '<div style="padding:6px 12px 10px;">%s</div>' % _type_rows),
  panel(phead('Components')
        + ('<div style="padding:12px;display:flex;flex-direction:column;gap:12px;flex:1 1 0;overflow:hidden;">'
           '<div style="display:flex;flex-direction:column;gap:6px;">%s'
           '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">%s%s%s%s%s</div></div>'
           '<div style="display:flex;flex-direction:column;gap:6px;">%s'
           '<div style="display:flex;gap:5px;flex-wrap:wrap;">%s%s%s%s%s</div></div>'
           '<div style="display:flex;flex-direction:column;gap:6px;">%s'
           '<div style="display:flex;gap:14px;flex-wrap:wrap;">%s%s%s%s%s%s</div></div>'
           '<div style="display:flex;flex-direction:column;gap:6px;">%s'
           '<div style="display:flex;gap:8px;">%s%s</div></div>'
           '<div style="display:flex;flex-direction:column;gap:6px;">%s%s</div>'
           '</div>') % (
           label('Buttons'), btn('Primary', 'primary', 'check'), btn('Solid', 'solid'), btn('Ghost', 'ghost', 'play'),
           btn('Danger', 'danger', 'x'), iconbtn('gear', 'Example icon button'),
           label('Chips'), chip('patchright'), chip('v8', AGENT), chip('llm', AGENT), chip('held', WARN), chip('mcp', ACC),
           label('Status'), status('ok'), status('drift'), status('fail'), status('run'), status('queued'), status('paused'),
           label('Inputs'), inp('css=.product-card', w='190px'), toggle(True, 'enabled'),
           label('Table'), table([('Scraper','120px','left'),('Rows','60px','right'),('Status','90px','left')],
                                 [[alink('hn-frontpage', '#'), mono('30'), status('ok')],
                                  [alink('shop-eu-prices', '#'), mono('118', 12, FAIL), status('drift')]])),
        grow=True),
  panel(phead('Rules')
        + ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:9px;flex:1 1 0;overflow:hidden;">%s</div>'
           % ''.join('<div style="display:flex;gap:8px;align-items:flex-start;">'
                     '<span style="color:%s;font-family:%s;font-size:10px;line-height:17px;flex-shrink:0;">●</span>'
                     '<span style="font-family:%s;font-size:11px;color:%s;line-height:1.55;">%s</span></div>' % (
                         c, MONO, MONO, TXT2, esc(t))
                     for c, t in [
                        (ACC, 'Density beats comfort. 28px table rows, 12px mono cells, tabular numerals.'),
                        (OK,  'Status is a glyph plus a word, never colour alone.'),
                        (AGENT, 'Purple marks anything an LLM authored: versions, rows, spend.'),
                        (WARN, 'Held delivery and pending approval both read amber, never red. Nothing is broken yet.'),
                        (FAIL, 'Red is reserved for a run that failed or a site that blocked us.'),
                        (TXT2, 'Real button, anchor, input and label elements even in a mockup, so Tab reaches them.'),
                        (TXT2, 'Text meets 4.5:1. text-3 is the floor and never goes below 10.5px.'),
                        (TXT2, 'No shadows, no gradients, no emoji. Icons are 1.75px stroke SVG.')]))),
  panel(phead('Screens', '', '16')
        + ('<div style="padding:8px 12px 11px;display:flex;flex-direction:column;gap:1px;flex:1 1 0;overflow:hidden;">%s</div>'
           % ''.join('<a href="%s" style="display:flex;align-items:center;gap:8px;padding:4px 0;text-decoration:none;">'
                     '<span style="font-family:%s;font-size:10px;color:%s;width:16px;">%02d</span>'
                     '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;">%s</span>%s</a>' % (
                         h, MONO, TXT3, i + 1, MONO, TXT2, n, icon('chev', 11, TXT3))
                     for i, (n, h) in enumerate([
                        ('Overview','Main.dc.html'), ('Scrapers','Scrapers.dc.html'),
                        ('Scraper detail','ScraperDetail.dc.html'), ('Records','Records.dc.html'),
                        ('Script & versions','Script.dc.html'), ('Runs','Runs.dc.html'),
                        ('Run detail','RunDetail.dc.html'), ('Run live','RunLive.dc.html'),
                        ('New scraper','NewScraper.dc.html'), ('Builder live','BuilderLive.dc.html'),
                        ('Repairs','Repairs.dc.html'), ('Repair review','RepairReview.dc.html'),
                        ('Network','Network.dc.html'), ('Delivery','Delivery.dc.html'),
                        ('Settings','Settings.dc.html'), ('Style guide','StyleGuide.dc.html')]))), grow=True))

add('StyleGuide.dc.html', 'Style guide', 'Settings',
    pagehead('Style guide', 'dense console · dark first', btn('Export tokens', 'ghost', 'download')), sg_body)


# ============================== 5b. SCRIPT (rebuilt with probe) ==============================
def dock_tab(t, on, badge=''):
    return ('<span style="display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 11px;'
            'font-family:%s;font-size:11.5px;color:%s;font-weight:%d;border-bottom:2px solid %s;">%s%s</span>') % (
        SANS, TXT if on else TXT2, 600 if on else 400, ACC if on else 'transparent', esc(t),
        chip(badge, TXT3) if badge else '')

_probe_rows = []
for fld, sel, matched, nonempty, sample, ok_ in [
    ('name','h3.title','42 / 42','42','Lavazza Qualita Rossa 1kg',True),
    ('price','.price','42 / 42','14','','FAIL'),
    ('price','[data-testid=price-now]','42 / 42','42','8.95',True),
    ('currency','.price [data-cur]','42 / 42','14','','FAIL'),
    ('url','a.detail','42 / 42','42','/p/lavazza-qualita-rossa-1kg',True)]:
    good = (ok_ is True)
    _probe_rows.append([
        mono(fld, 11.5, TXT if good else FAIL),
        '<span style="font-family:%s;font-size:11.5px;color:%s;">%s</span>' % (MONO, SY_KEY if good else TXT3, esc(sel)),
        mono(matched, 11.5, TXT2),
        mono(nonempty, 12, OK if good else FAIL),
        '<span style="font-family:%s;font-size:11px;color:%s;">%s</span>' % (MONO, SY_STR if sample else TXT3,
                                                                            esc(sample) if sample else 'empty'),
        ('<div style="display:flex;gap:4px;justify-content:flex-end;">%s%s</div>' % (
            smallbtn('Use', ACC), smallbtn('Fallback'))) if good else smallbtn('Why empty?', WARN)])

_script_dock = ('<div style="height:252px;flex-shrink:0;display:flex;flex-direction:column;border-top:1px solid %s;'
  'background:%s;">'
  '<div style="height:32px;flex-shrink:0;display:flex;align-items:flex-end;padding:0 10px;gap:0;'
  'border-bottom:1px solid %s;background:%s;">%s%s%s%s<span style="flex-grow:1;"></span>%s</div>'
  '<div style="flex:1 1 0;display:flex;flex-direction:column;overflow:hidden;">'
    '<div style="display:flex;align-items:center;gap:7px;padding:8px 11px;border-bottom:1px solid %s;">'
      '<span style="font-family:%s;font-size:10.5px;color:%s;">probe</span>'
      '<label for="probe-q" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);">Probe selector</label>'
      '<input id="probe-q" type="text" value="[data-testid=price-now]" style="flex-grow:1;height:26px;padding:0 9px;'
      'background:%s;border:1px solid %s;border-radius:4px;color:%s;outline:none;font-family:%s;font-size:11.5px;">'
      '%s%s</div>'
    '<div style="flex:1 1 0;overflow:hidden;">%s</div></div></div>') % (
  LINE, SURF, LINE, SURF2,
  dock_tab('Fields & probe', True), dock_tab('Rows', False, '452'),
  dock_tab('Validation', False, '6'), dock_tab('Test log', False),
  smallbtn('Re-probe all'),
  LINE, MONO, TXT3, SURF2, LINE2, TXT, MONO,
  btn('Probe', 'solid', 'search', small=True), btn('Pick on page', 'ghost', 'eye', small=True),
  table([('Field','90px','left'),('Selector','260px','left'),('Matched','80px','right'),
         ('Non-empty','80px','right'),('First value','250px','left'),('','140px','right')], _probe_rows))

_script_browser = ('<div style="padding:10px 11px;display:flex;flex-direction:column;gap:8px;flex:1 1 0;'
  'overflow:hidden;">'
  '<div style="display:flex;align-items:center;gap:6px;">'
  '<span style="font-family:%s;font-size:10.5px;color:%s;flex-grow:1;overflow:hidden;text-overflow:ellipsis;'
  'white-space:nowrap;">shop.example.eu/pricing?page=1</span>%s</div>'
  '%s'
  '<div style="display:flex;flex-direction:column;gap:5px;">%s%s%s</div>'
  '<div style="margin-top:auto;font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">%s Highlighted nodes are '
  'the 42 that match the probe. The page is the same context the runner uses, profile and proxy included.</div></div>') % (
  MONO, TXT2, iconbtn('refresh', 'Reload page'),
  fake_page(w=296, h=176),
  kv('profile', chip('shop-eu-login · restored', TXT2)),
  kv('proxy', chip('eu-resi · 85.203.x.x', TXT2)),
  kv('highlighted', mono('42 nodes', 11.5, ACC)),
  MONO, TXT3, icon('eye', 11, TXT3))

_vers_compact = _vers
script_body2 = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="width:182px;flex-shrink:0;display:flex;">%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:322px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div>') % (
  panel(phead('Versions', iconbtn('plus', 'New version from active'))
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % _vers_compact, grow=True, style='flex:1 1 0;'),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:9px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:500;">shop-eu-prices.yaml</span>%s'
         '<span style="flex-grow:1;"></span>'
         '<span style="font-family:%s;font-size:10.5px;color:%s;">52 lines</span>%s</div>') % (
        LINE, SURF2, MONO, TXT, chip('v7 active', OK), MONO, TXT3, iconbtn('ext', 'Open in external editor'))
        + code_block(YAML_V7, marks={23: '#241D10', 27: '#241D10'})
        + _script_dock, grow=True, style='flex:1 1 0;'),
  panel(phead('Live page', chip('headed', ACC), 'probe target') + _script_browser, grow=True, style='flex:1 1 0;'),
  panel(phead('Test & promote', '', 'auto_if_minor') + _testpanel))

add('Script.dc.html', 'Script & probe', 'Scrapers',
    pagehead('shop-eu-prices', 'script v7',
             btn('Save version', 'primary', 'check') + btn('Test run', 'ghost', 'play')
             + btn('Diff vs v6', 'ghost', 'branch') + iconbtn('download', 'Download YAML'),
             tabs=[('Overview',False),('Script',True),('Schema',False),('Runs',False),('Delivery',False),('Settings',False)]),
    script_body2)

# ============================== 12b. REPAIR REVIEW (impact + track record) ==============================
_rr_impact = ('<div style="padding:5px 12px 10px;">%s%s%s%s%s%s</div>') % (
  kv('delivers to', chip('webhook', TXT2) + ' ' + chip('s3', TXT2) + ' ' + chip('mcp', ACC)),
  kv('rows per run', mono('~452', 11.5) + ' ' + mono('hourly', 11, TXT3)),
  kv('downstream', mono('2 consumers', 11.5, TXT2)),
  kv('schema change', chip('none · same 4 fields', OK)),
  kv('agent here', mono('4 approved', 11.5, OK) + ' ' + mono('1 rejected', 11, FAIL)),
  kv('agent overall', mono('11 of 12 kept', 11.5, TXT2) + ' ' + mono('30d', 11, TXT3)))

rr_body2 = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:392px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s%s%s</div></div>') % (
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:8px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">Unified diff</span>%s%s'
         '<span style="flex-grow:1;"></span>%s%s</div>') % (LINE, SURF2, SANS, TXT2,
        chip('+3 −3', TXT2), chip('3 hunks', TXT3), btn('Side by side', 'ghost', small=True),
        btn('Open in editor', 'ghost', 'code', 'Script.dc.html', small=True))
        + diff_block(DIFF)
        + ('<div style="flex-shrink:0;border-top:1px solid %s;background:%s;padding:10px 12px;display:flex;'
           'align-items:center;gap:9px;">%s<span style="flex-grow:1;"></span>%s%s%s%s</div>') % (
           LINE, SURF2, status('ok', 'test run passed · 452 rows · 6 of 6 rules'),
           btn('Reject', 'danger', 'x'), btn('Test again', 'ghost', 'refresh'),
           btn('Edit, then approve', 'solid', 'code', 'Script.dc.html'),
           btn('Approve & promote', 'primary', 'check', 'ScraperDetail.dc.html')),
        grow=True, style='flex:1 1 0;'),
  panel(phead('Why', '', 'candidate v8') + _rr_why),
  panel(phead('Validation, before and after', chip('2 fixed', OK))
        + '<div style="padding:4px 12px 10px;">%s</div>' % _rr_valid),
  panel(phead('Impact & track record', '', 'before you approve') + _rr_impact),
  panel(phead('Sample rows from the test run', '', 'first 5 of 452')
        + '<div style="overflow:hidden;">%s</div>' % table(
            [('name','210px','left'),('price','70px','right'),('cur','48px','left')],
            [['<span style="font-family:%s;font-size:11px;color:%s;">%s</span>' % (MONO, TXT, esc(n)),
              mono(p, 11.5, OK), mono(c, 11, TXT3)]
             for n, p, c in [('Lavazza Qualita Rossa 1kg','8.95','EUR'), ('Illy Classico Beans 1kg','18.40','EUR'),
                             ('Segafredo Intermezzo 1kg','12.49','EUR'), ('Kimbo Napoletano 1kg','11.20','EUR'),
                             ('Pellini Top 1kg','13.75','EUR')]]), grow=True))

add('RepairReview.dc.html', 'Repair review', 'Repairs',
    pagehead('shop-eu-prices  v7 → v8', 'proposed 2 minutes ago after run #4182',
             chip('auto_if_minor', WARN) + btn('Open run #4182', 'ghost', 'ext', 'RunDetail.dc.html')),
    rr_body2)

# ============================== 17. SCHEMA & FIELD HEALTH ==============================
_fields = []
for f, ty, req, since, nullr, dist, samp, ok_ in [
    ('name','string','required','v1','0.00','452','Lavazza Qualita Rossa 1kg',True),
    ('price','number','required','v1','0.66','40','8.95',False),
    ('currency','string','optional','v5','0.66','1','EUR',False),
    ('url','string','required · unique','v1','0.00','452','/p/lavazza-qualita-rossa-1kg',True)]:
    _fields.append([mono(f, 12, TXT if ok_ else FAIL), chip(ty, TXT3), mono(req, 11, TXT2), chip(since, TXT3),
                    mono(nullr, 12, OK if ok_ else FAIL), mono(dist, 11.5, TXT2),
                    '<span style="font-family:%s;font-size:11px;color:%s;">%s</span>' % (MONO, SY_STR, esc(samp)),
                    status('ok', '') if ok_ else status('fail', '')])

_health = ''
for f, series, note in [
    ('name', [0]*30, 'clean for 30 days'),
    ('price', [0]*26 + [0, 0, 31, 34], 'broke 2 runs ago'),
    ('currency', [0]*26 + [0, 0, 31, 34], 'nested in price, broke with it'),
    ('url', [0]*30, 'clean for 30 days')]:
    bars = ''
    for v in series:
        h = max(2, int(v / 40 * 26))
        bars += '<div style="flex:1 1 0;height:%dpx;background:%s;align-self:flex-end;border-radius:1px;"></div>' % (
            h, FAIL if v > 5 else (LINE2 if v == 0 else WARN))
    _health += ('<div style="display:flex;flex-direction:column;gap:4px;padding:7px 0;border-bottom:1px solid %s;">'
      '<div style="display:flex;align-items:baseline;gap:8px;">'
      '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:500;width:70px;">%s</span>'
      '<span style="font-family:%s;font-size:10.5px;color:%s;flex-grow:1;">%s</span></div>'
      '<div style="display:flex;align-items:flex-end;gap:1.5px;height:26px;">%s</div></div>') % (
      LINE, MONO, TXT, esc(f), MONO, TXT3, esc(note), bars)

_schema_hist = ''
for v, when, change, kind in [
    ('v8','pending','price and currency selectors moved, no field change','agent'),
    ('v5','28d ago','+ currency (optional)','agent'),
    ('v3','41d ago','url marked unique','ok'),
    ('v1','2 aug 2026','name, price, url','ok')]:
    c = AGENT if kind == 'agent' else TXT3
    _schema_hist += ('<div style="display:flex;gap:9px;padding:8px 0;border-bottom:1px solid %s;">'
      '<span style="font-family:%s;font-size:11px;color:%s;width:34px;flex-shrink:0;font-weight:600;">%s</span>'
      '<div style="flex-grow:1;min-width:0;display:flex;flex-direction:column;gap:2px;">'
      '<span style="font-family:%s;font-size:11px;color:%s;line-height:1.45;">%s</span>'
      '<span style="font-family:%s;font-size:10px;color:%s;">%s</span></div></div>') % (
      LINE, MONO, c, v, MONO, TXT2, esc(change), MONO, TXT3, when)

schema_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;'
  'flex-direction:column;">%s'
  '<div style="display:flex;gap:12px;flex:1 1 0;min-height:0;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="width:330px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div></div>') % (
  banner('drift', 'price and currency have been null on a third of rows since 12:04. Downstream consumers reading '
                  'this schema will see nulls, not an error. Repair v8 fixes both and is waiting for approval.',
         smallbtn('Review v8', ACC, 'RepairReview.dc.html') + smallbtn('Notify consumers')),
  panel(phead('Fields', btn('Edit schema', 'ghost', 'code', 'Script.dc.html', small=True), 'contract v7 · 4 fields')
        + '<div style="overflow:hidden;">%s</div>' % table(
            [('Field','110px','left'),('Type','90px','left'),('Constraint','130px','left'),('Since','60px','left'),
             ('Null rate','80px','right'),('Distinct','70px','right'),('Latest value','290px','left'),('','50px','left')],
            _fields)),
  panel(phead('Null rate per field, 30 days', '', 'one bar per run day')
        + '<div style="padding:4px 12px 10px;flex:1 1 0;overflow:hidden;">%s</div>' % _health, grow=True),
  panel(phead('Schema history', '', '4 changes')
        + '<div style="padding:4px 12px 10px;">%s</div>' % _schema_hist),
  panel(phead('Freshness & consumers')
        + ('<div style="padding:5px 12px 11px;">%s%s%s%s%s%s'
           '<div style="border-top:1px solid %s;margin-top:6px;padding-top:9px;font-family:%s;font-size:10.5px;'
           'color:%s;line-height:1.5;">%s A consumer that pins to contract v7 keeps the same four field names. '
           'Only a field rename or removal bumps the contract.</div></div>') % (
           kv('last clean run', mono('10:04', 11.5, WARN) + ' ' + mono('2h ago', 11, TXT3)),
           kv('last run', mono('12:04', 11.5) + ' ' + chip('fallback rows', AGENT)),
           kv('rows total', mono('184,204', 11.5)),
           kv('webhook consumer', status('ok', 'reading')),
           kv('s3 consumer', status('ok', 'reading')),
           kv('mcp sheets', status('fail', 'retrying')),
           LINE, MONO, TXT3, icon('file', 11, TXT3)), grow=True))

add('Schema.dc.html', 'Schema & field health', 'Records',
    pagehead('shop-eu-prices', 'output schema · contract v7',
             btn('Export JSON Schema', 'ghost', 'download') + btn('Open records', 'ghost', 'db', 'Records.dc.html'),
             tabs=[('Overview',False),('Script',False),('Schema',True),('Runs',False),('Delivery',False),('Settings',False)]),
    schema_body)

# ============================== 18. COVERAGE ==============================
RUNGS = ['http', 'patchright', '+ proxy', 'camoufox', 'byparr']
COV = [
 ('example-products', [(98, 640), (0, 0), (0, 0), (0, 0), (0, 0)], 'http is enough'),
 ('hn-frontpage',     [(100, 672), (0, 0), (0, 0), (0, 0), (0, 0)], 'http is enough'),
 ('gov-tenders',      [(97, 28), (0, 0), (0, 0), (0, 0), (0, 0)], 'http is enough'),
 ('competitor-skus',  [(62, 96), (91, 42), (96, 18), (0, 0), (0, 0)], 'start at patchright, save 38% of attempts'),
 ('vendor-stock',     [(0, 12), (99, 168), (0, 0), (0, 0), (0, 0)], 'needs a browser, no proxy'),
 ('shop-eu-prices',   [(0, 24), (41, 64), (94, 132), (0, 0), (0, 0)], 'needs proxy, skip the first two rungs'),
 ('job-board-eu',     [(0, 6), (0, 8), (58, 12), (72, 9), (0, 0)], 'marginal, sso expires often'),
 ('news-archive',     [(0, 18), (0, 22), (0, 16), (33, 9), (17, 6)], 'losing, consider a hosted browser'),
]
_cov = ''
_cov += ('<div style="display:flex;gap:6px;align-items:center;padding:0 12px 7px;">'
         '<span style="width:150px;flex-shrink:0;"></span>%s'
         '<span style="width:230px;flex-shrink:0;"></span></div>') % ''.join(
    '<span style="flex:1 1 0;text-align:center;font-family:%s;font-size:10px;color:%s;letter-spacing:.06em;'
    'text-transform:uppercase;">%s</span>' % (SANS, TXT3, esc(r)) for r in RUNGS)
for name, cells, advice in COV:
    _cov += ('<div style="display:flex;gap:6px;align-items:center;padding:4px 12px;border-bottom:1px solid %s;">'
             '<a href="ScraperDetail.dc.html" style="width:150px;flex-shrink:0;font-family:%s;font-size:11.5px;'
             'color:%s;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">%s</a>'
             '%s'
             '<span style="width:230px;flex-shrink:0;font-family:%s;font-size:10.5px;color:%s;padding-left:10px;'
             'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">%s</span></div>') % (
        LINE, MONO, TXT, esc(name),
        ''.join(grid_cell(p, t, t) if t else grid_cell(0, 0, 0) for p, t in cells),
        MONO, TXT3, esc(advice))

_blocks = ''
for reason, n, pct, c in [('cloudflare managed challenge', 28, 46, FAIL), ('datadome', 11, 18, FAIL),
                          ('http 429 rate limit', 9, 15, WARN), ('captcha · turnstile', 7, 11, WARN),
                          ('empty body, js only', 4, 7, WARN), ('http 403, no vendor detected', 2, 3, TXT3)]:
    _blocks += ('<div style="display:flex;flex-direction:column;gap:4px;padding:6px 0;border-bottom:1px solid %s;">'
      '<div style="display:flex;align-items:baseline;gap:8px;">'
      '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;">%s</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;font-variant-numeric:tabular-nums;">%d</span></div>'
      '<div style="height:4px;background:%s;border-radius:2px;overflow:hidden;">'
      '<div style="width:%d%%;height:100%%;background:%s;"></div></div></div>') % (
      LINE, MONO, TXT2, esc(reason), MONO, c, n, SURF2, pct, c)

cov_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:330px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div>') % (
  panel(phead('Success by scraper and engine', btn('Apply suggested ladders', 'ghost', 'check', small=True),
              'last 30 days · percent, attempts below')
        + '<div style="padding:10px 0 4px;flex:1 1 0;overflow:hidden;">%s</div>' % _cov
        + ('<div style="margin-top:auto;border-top:1px solid %s;background:%s;padding:9px 12px;font-family:%s;'
           'font-size:10.5px;color:%s;line-height:1.5;">%s A dash means the rung was never reached. Starting a '
           'scraper at the rung that actually works removes wasted attempts, which is most of the block count '
           'and most of the proxy spend.</div>') % (LINE, SURF2, MONO, TXT3, icon('zap', 11, ACC)),
        grow=True, style='flex:1 1 0;'),
  panel(phead('Why we were blocked', '', '61 blocks · 30 days')
        + '<div style="padding:5px 12px 10px;">%s</div>' % _blocks),
  panel(phead('Suggested changes', chip('3', ACC))
        + ('<div style="padding:11px 12px;display:flex;flex-direction:column;gap:10px;flex:1 1 0;">%s'
           '<div style="margin-top:auto;font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">%s '
           'These are measured from this install only. Nothing here is a general claim about what beats '
           'which vendor.</div></div>') % (
           ''.join('<div style="display:flex;flex-direction:column;gap:3px;padding:7px 9px;background:%s;'
                   'border:1px solid %s;border-radius:4px;">'
                   '<span style="font-family:%s;font-size:11px;color:%s;font-weight:500;">%s</span>'
                   '<span style="font-family:%s;font-size:10.5px;color:%s;line-height:1.45;">%s</span></div>' % (
                       SURF2, LINE, MONO, TXT, esc(t), MONO, TXT3, esc(d))
                   for t, d in [('shop-eu-prices → start at + proxy', 'skips 88 attempts a month that never pass'),
                                ('competitor-skus → start at patchright', 'http passes 62% and costs a retry each time'),
                                ('news-archive → hosted browser or drop', 'no rung above 33% in 30 days')]),
           MONO, TXT3, icon('alert', 11, WARN)), grow=True))

add('Coverage.dc.html', 'Coverage', 'Network',
    pagehead('Coverage', 'which engine works on which site',
             btn('Export CSV', 'ghost', 'download') + btn('Back to network', 'ghost', 'chev', 'Network.dc.html')),
    cov_body)

def json_block(src, height=None):
    rows = ''
    for i, ln in enumerate(src.split('\n')):
        h = _escq(ln)
        h = _re.sub(r'(&quot;[\w_]+&quot;)(\s*:)', r'<span style="color:%s;">\1</span><span style="color:%s;">\2</span>' % (SY_KEY, SY_PUN), h)
        h = _re.sub(r':(\s*)(&quot;[^&]*&quot;)', r':\1<span style="color:%s;">\2</span>' % SY_STR, h)
        h = _re.sub(r'\b(\d+(?:\.\d+)?|true|false|null)\b', r'<span style="color:%s;">\1</span>' % SY_NUM, h)
        rows += ('<div style="display:flex;min-height:17px;">'
                 '<span style="width:28px;flex-shrink:0;text-align:right;padding-right:9px;color:%s;font-family:%s;'
                 'font-size:10px;line-height:17px;font-variant-numeric:tabular-nums;">%d</span>'
                 '<span style="font-family:%s;font-size:11px;line-height:17px;white-space:pre;color:%s;">%s</span>'
                 '</div>') % (TXT3, MONO, i + 1, MONO, TXT2, h)
    hs = ('height:%dpx;' % height) if height else 'flex:1 1 0;'
    return '<div style="%soverflow:hidden;padding:7px 0;background:%s;">%s</div>' % (hs, BG, rows)

# ============================== 19. MCP CONSOLE ==============================
MCP_TOOLS = [('list_scrapers', False), ('get_scraper', False), ('create_scraper', False), ('run_scraper', False),
             ('get_run', False), ('get_results', True), ('search_results', False), ('get_pending_repairs', False),
             ('approve_repair', False)]
_mcp_list = ''
for t, on in MCP_TOOLS:
    _mcp_list += ('<div style="display:flex;align-items:center;gap:7px;padding:7px 11px;border-bottom:1px solid %s;'
                  'background:%s;border-left:2px solid %s;">'
                  '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:%d;">%s</span></div>') % (
        LINE, SURF3 if on else 'transparent', ACC if on else 'transparent', MONO, TXT if on else TXT2, 600 if on else 400, esc(t))

REQ = '''{
  "scraper": "shop-eu-prices",
  "run": "latest",
  "limit": 3,
  "include_fallback": false
}'''
RES = '''{
  "run": 4169,
  "extracted_at": "2026-09-17T10:04:29Z",
  "contract": "v7",
  "stale": true,
  "stale_reason": "last clean run 2h ago, v8 pending",
  "rows": [
    {"name": "Lavazza Qualita Rossa 1kg", "price": 8.95,
     "currency": "EUR", "url": "https://shop.example.eu/p/lqr-1kg",
     "source": "script"},
    {"name": "Illy Classico Beans 1kg", "price": 18.40,
     "currency": "EUR", "url": "https://shop.example.eu/p/icb-1kg",
     "source": "script"}
  ],
  "returned": 2,
  "truncated": false
}'''

_map_rows = []
for src, arg, ty, note in [('name','values[0]','string','as-is'), ('price','values[1]','number','2 decimals'),
                           ('currency','values[2]','string','as-is'), ('url','values[3]','string','absolute'),
                           ('$run.extracted_at','values[4]','string','iso 8601'),
                           ('$row.source','values[5]','string','script or llm')]:
    _map_rows.append([mono(src, 11, ACC if src.startswith('$') else TXT),
                      mono(arg, 11, TXT2), chip(ty, TXT3), mono(note, 10.5, TXT3)])

mcp_body = ('<div style="padding:16px;display:flex;gap:12px;flex:1 1 0;min-height:0;box-sizing:border-box;">'
  '<div style="width:206px;flex-shrink:0;display:flex;">%s</div>'
  '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:12px;">%s%s</div>'
  '<div style="width:386px;flex-shrink:0;display:flex;">%s</div></div>') % (
  panel(phead('Inbound tools', '', '9')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % _mcp_list, grow=True, style='flex:1 1 0;'),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:9px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">get_results</span>%s'
         '<span style="flex-grow:1;"></span>%s%s</div>') % (LINE, SURF2, MONO, TXT,
        chip('request', TXT3), btn('Send', 'primary', 'send', small=True), smallbtn('Load example'))
        + json_block(REQ, height=110)
        + ('<div style="border-top:1px solid %s;background:%s;padding:7px 12px;display:flex;align-items:center;'
           'gap:8px;flex-shrink:0;">%s<span style="font-family:%s;font-size:10.5px;color:%s;">'
           'scraper, run, limit, offset, include_fallback · all optional but scraper</span></div>') % (
           LINE, SURF2, chip('arguments', TXT3), MONO, TXT3), grow=True),
  panel(('<div style="height:34px;flex-shrink:0;padding:0 12px;display:flex;align-items:center;gap:9px;'
         'border-bottom:1px solid %s;background:%s;">'
         '<span style="font-family:%s;font-size:11.5px;color:%s;font-weight:600;">Response</span>%s%s'
         '<span style="flex-grow:1;"></span>%s</div>') % (LINE, SURF2, SANS, TXT2,
        status('ok', '200 · 41 ms'), chip('stale: true', WARN), iconbtn('copy', 'Copy response'))
        + json_block(RES), grow=True, style='flex:1 1 0;'),
  panel(phead('Outbound mapping', btn('Test send', 'ghost', 'send', small=True), 'sheets-server · append_rows')
        + ('<div style="padding:5px 12px 11px;display:flex;flex-direction:column;gap:9px;flex:1 1 0;">'
           '%s%s'
           '<div style="border:1px solid %s;border-radius:4px;overflow:hidden;">%s</div>'
           '%s%s%s'
           '<div style="margin-top:auto;font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">%s Rows are sent '
           'in batches. A failed batch retries with backoff and the run stays undelivered until it lands or the '
           'retry budget runs out.</div></div>') % (
           kv('server', chip('sheets-server · stdio', TXT2)),
           kv('tool', chip('append_rows', ACC)),
           LINE, table([('Record field','130px','left'),('Tool argument','100px','left'),
                        ('Type','76px','left'),('Transform','110px','left')], _map_rows),
           kv('batch size', mono('500 rows', 11.5)),
           kv('on failure', chip('retry 5 · backoff', WARN)),
           kv('last send', status('fail', 'timeout · retry 2 of 5')),
           MONO, TXT3, icon('send', 11, TXT3)), grow=True, style='flex:1 1 0;'))

add('McpConsole.dc.html', 'MCP console', 'Delivery',
    pagehead('MCP', 'inbound tools and outbound mapping',
             chip('http://127.0.0.1:8088/mcp', TXT2) + btn('Rotate token', 'ghost', 'key')
             + btn('Back to delivery', 'ghost', 'chev', 'Delivery.dc.html')),
    mcp_body)

# ============================== 20. AUDIT ==============================
_audit = []
for when, who, act, obj, detail, kind in [
    ('12:04:41','repair agent','proposed version','shop-eu-prices v8','1 selector changed, classified minor','agent'),
    ('12:04:18','system','delivered','run #4182 → webhook','118 rows, 202','ok'),
    ('12:04:10','fallback agent','extracted rows','run #4182','118 rows via sonnet-5, $0.09','agent'),
    ('12:04:09','system','failed validation','run #4182','max_null_rate.price 0.66','fail'),
    ('11:58:02','you','ran manually','gov-tenders','from the scrapers list','ok'),
    ('11:41:10','you','read secret','capmonster_key','shown in settings','drift'),
    ('11:20:33','you','edited config','competitor-skus','schedule 6h → 4h','ok'),
    ('10:02:14','you','approved version','vendor-stock v4','auto_if_minor, reviewed','ok'),
    ('09:44:51','you','added custom_python','job-board-eu v2','step 4, decodes an obfuscated email','drift'),
    ('09:12:07','builder agent','created scraper','gov-tenders v1','14 turns, $0.62','agent'),
    ('08:55:40','you','rotated token','mcp bearer','previous token revoked','ok'),
    ('08:31:19','system','backup completed','smartscraper.db','412 MB → ./backups/','ok'),
    ('07:02:55','you','rejected version','news-archive v2','blocked before extraction','fail'),
    ('06:00:04','system','budget alert','monthly','80% of $60 forecast','drift'),
]:
    c = {'agent': AGENT, 'ok': TXT2, 'fail': FAIL, 'drift': WARN}[kind]
    _audit.append([mono(when, 11, TXT3),
                   chip(who, AGENT if 'agent' in who else (ACC if who == 'you' else TXT3)),
                   '<span style="font-family:%s;font-size:11.5px;color:%s;">%s</span>' % (MONO, c, esc(act)),
                   mono(obj, 11, TXT), mono(detail, 11, TXT3)])

_pyinv = ''
for scr, ver, step, what, who in [
    ('job-board-eu','v2','step 4','decodes an obfuscated email attribute','you · today 09:44'),
    ('vendor-stock','v4','step 7','parses a packed js array of stock levels','you · 14 sep')]:
    _pyinv += ('<div style="display:flex;flex-direction:column;gap:3px;padding:8px 0;border-bottom:1px solid %s;">'
      '<div style="display:flex;align-items:center;gap:7px;">'
      '<span style="color:%s;font-family:%s;font-size:10px;">◆</span>'
      '<a href="Script.dc.html" style="font-family:%s;font-size:11.5px;color:%s;font-weight:500;'
      'text-decoration:none;">%s</a>%s%s</div>'
      '<span style="font-family:%s;font-size:10.5px;color:%s;line-height:1.45;">%s</span>'
      '<span style="font-family:%s;font-size:10px;color:%s;">added by %s</span></div>') % (
      LINE, AGENT, MONO, MONO, TXT, esc(scr), chip(ver, TXT3), chip(step, TXT3), MONO, TXT2, esc(what), MONO, TXT3, esc(who))

audit_body = ('<div style="%s">'
  '<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">%s%s%s%s%s'
  '<span style="flex-grow:1;"></span>%s</div>'
  '<div style="display:flex;gap:12px;flex:1 1 0;min-height:0;">'
  '<div style="flex:1 1 0;min-width:0;display:flex;">%s</div>'
  '<div style="width:330px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;">%s%s</div></div></div>') % (
  PAD,
  btn('Everyone', 'solid', 'filter'), btn('You', 'ghost'), btn('Agents', 'ghost'), btn('System', 'ghost'),
  btn('Today', 'ghost', 'calendar'), btn('Export', 'ghost', 'download'),
  panel(phead('Audit log', '', '1,842 entries · append only')
        + '<div style="flex:1 1 0;overflow:hidden;">%s</div>' % table(
            [('When','70px','left'),('Actor','120px','left'),('Action','180px','left'),
             ('Object','220px','left'),('Detail','360px','left')], _audit)
        + ('<div style="margin-top:auto;height:32px;flex-shrink:0;border-top:1px solid %s;background:%s;display:flex;'
           'align-items:center;padding:0 12px;"><span style="font-family:%s;font-size:11px;color:%s;">'
           'showing 14 of 1,842 · kept 365 days · entries are never edited or deleted</span></div>') % (
           LINE, SURF2, MONO, TXT3), grow=True, style='flex:1 1 0;'),
  panel(phead('custom_python in use', chip('2 scrapers', AGENT), 'unsandboxed')
        + ('<div style="padding:4px 12px 10px;">%s'
           '<div style="padding-top:9px;font-family:%s;font-size:10.5px;color:%s;line-height:1.5;">%s These steps '
           'run as your user inside the run subprocess, with no sandbox beyond its timeout. Every change to one '
           'needs approval regardless of the scraper’s promotion policy.</div></div>') % (
           _pyinv, MONO, TXT3, icon('alert', 11, WARN))),
  panel(phead('Retention & integrity')
        + ('<div style="padding:5px 12px 11px;">%s%s%s%s%s</div>') % (
            kv('entries', mono('1,842', 11.5)), kv('oldest', mono('2 aug 2026', 11.5)),
            kv('keep for', mono('365 days', 11.5)), kv('exported', chip('never', TXT3)),
            kv('secret reads 30d', mono('4', 11.5, WARN))), grow=True))

add('Audit.dc.html', 'Audit log', 'Settings',
    pagehead('Audit log', 'who did what, and which of it was an agent', ''), audit_body)

# ============================== 21. FIRST RUN ==============================
_start_cards = ''
for ic, t, d, cta, href in [
    ('zap','Describe a page','Give a URL and say what you want in plain language. The builder explores it and writes the script.','Start builder','NewScraper.dc.html'),
    ('file','Import a YAML script','Paste or drop a script you already have. It is validated against the step schema before it runs.','Import','NewScraper.dc.html'),
    ('db','Start from an example','Three worked scrapers: a product grid, a paginated table and a login-gated list.','Browse examples','NewScraper.dc.html')]:
    _start_cards += ('<a href="%s" style="flex:1 1 0;display:flex;flex-direction:column;gap:9px;padding:16px;'
      'background:%s;border:1px solid %s;border-radius:4px;text-decoration:none;min-width:0;">'
      '<span style="width:28px;height:28px;border-radius:5px;border:1px solid %s;display:flex;align-items:center;'
      'justify-content:center;color:%s;">%s</span>'
      '<span style="font-family:%s;font-size:13px;color:%s;font-weight:600;">%s</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;line-height:1.55;flex-grow:1;">%s</span>'
      '<span style="font-family:%s;font-size:11px;color:%s;display:inline-flex;align-items:center;gap:5px;">%s%s</span>'
      '</a>') % (href, SURF, LINE, LINE2, ACC, icon(ic, 15, ACC), SANS, TXT, esc(t), MONO, TXT3, esc(d),
                 MONO, ACC, esc(cta), icon('chev', 11, ACC))

fr_body = ('<div style="%s">'
  '<div style="display:flex;gap:12px;flex-shrink:0;">%s%s%s%s%s</div>'
  '<div style="flex:1 1 0;min-height:0;display:flex;">%s</div></div>') % (
  PAD,
  tile('scrapers', '0', 'none yet', TXT3, ic='db'), tile('runs today', '0', 'nothing scheduled', TXT3, ic='play'),
  tile('rows today', '0', '', TXT3, ic='file'), tile('pending repairs', '0', '', TXT3, ic='branch'),
  tile('spend mtd', '$0.00', 'budget $60.00', TXT3, ic='zap'),
  panel(phead('Get started', '', 'nothing has run yet')
        + ('<div style="flex:1 1 0;display:flex;flex-direction:column;align-items:center;justify-content:center;'
           'gap:22px;padding:24px 40px;">'
           '<div style="display:flex;flex-direction:column;align-items:center;gap:8px;max-width:620px;">'
           '<span style="width:38px;height:38px;border:1.5px solid %s;border-radius:8px;display:flex;'
           'align-items:center;justify-content:center;color:%s;">%s</span>'
           '<h2 style="margin:0;font-family:%s;font-size:19px;font-weight:600;color:%s;">No scrapers yet</h2>'
           '<p style="margin:0;font-family:%s;font-size:12px;color:%s;line-height:1.65;text-align:center;">%s</p></div>'
           '<div style="display:flex;gap:12px;width:100%%;max-width:900px;">%s</div>'
           '<div style="display:flex;gap:26px;align-items:flex-start;max-width:900px;">%s</div></div>') % (
           ACC, ACC, icon('term', 19, ACC, 1.8), SANS, TXT, MONO, TXT3,
           esc('An agent writes the script once. After that the script runs on a schedule with no model in the loop, '
               'and the agent is only called back when a run fails its own validation.'),
           _start_cards,
           ''.join('<div style="flex:1 1 0;display:flex;flex-direction:column;gap:5px;">'
                   '<span style="font-family:%s;font-size:10px;color:%s;letter-spacing:.09em;text-transform:uppercase;'
                   'font-weight:600;">%s</span>'
                   '<span style="font-family:%s;font-size:10.5px;color:%s;line-height:1.55;">%s</span></div>' % (
                       SANS, TXT3, s, MONO, TXT3, d)
                   for s, d in [('Build once', 'The agent probes selectors and writes a versioned YAML step list.'),
                                ('Run cheaply', 'The runner executes that script. No tokens are spent on a normal run.'),
                                ('Validate', 'Schema, row count band and null rates decide whether a run passed.'),
                                ('Repair', 'A failure calls the agent back with the broken page and the report.')])),
        grow=True, style='flex:1 1 0;'))

add('FirstRun.dc.html', 'First run', 'Overview',
    pagehead('Overview', 'nothing configured', btn('New scraper', 'primary', 'plus', 'NewScraper.dc.html')), fr_body)

# ============================== 22. COMMAND PALETTE ==============================
_pal_rows = ''
for grp, items in [('Go to', [('Scrapers', 'g s'), ('Runs', 'g r'), ('Repairs  2 pending', 'g p')]),
                   ('Actions', [('Run shop-eu-prices now', '↵'), ('Approve repair v8', 'a'),
                                ('Acknowledge news-archive', 'k'), ('Mute competitor-skus for 24h', 'm')]),
                   ('Search', [('records where price is null', '/'), ('runs failed today', '/')])]:
    _pal_rows += ('<div style="padding:7px 13px 4px;font-family:%s;font-size:9.5px;color:%s;letter-spacing:.09em;'
                  'text-transform:uppercase;font-weight:600;">%s</div>') % (SANS, TXT3, esc(grp))
    for i, (t, k) in enumerate(items):
        on = (grp == 'Actions' and i == 1)
        _pal_rows += ('<div style="display:flex;align-items:center;gap:9px;padding:7px 13px;background:%s;'
          'border-left:2px solid %s;">'
          '<span style="color:%s;font-family:%s;font-size:10px;">%s</span>'
          '<span style="font-family:%s;font-size:12px;color:%s;flex-grow:1;">%s</span>'
          '<span style="font-family:%s;font-size:10px;color:%s;padding:1px 5px;border:1px solid %s;'
          'border-radius:3px;">%s</span></div>') % (
          SURF3 if on else 'transparent', ACC if on else 'transparent',
          ACC if on else TXT3, MONO, '▸' if on else '·', MONO, TXT if on else TXT2, esc(t),
          MONO, TXT3, LINE2, esc(k))

_palette = ('<div style="position:absolute;inset:0;background:rgba(6,8,12,.72);display:flex;align-items:flex-start;'
  'justify-content:center;padding-top:74px;">'
  '<div style="width:560px;background:%s;border:1px solid %s;border-radius:6px;overflow:hidden;display:flex;'
  'flex-direction:column;">'
  '<div style="display:flex;align-items:center;gap:9px;padding:0 13px;height:44px;border-bottom:1px solid %s;">%s'
  '<label for="pal" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);">Command</label>'
  '<input id="pal" type="text" value="app" placeholder="type a command, scraper or query"'
  ' style="flex-grow:1;background:transparent;border:0;outline:none;color:%s;font-family:%s;font-size:14px;">'
  '<span style="font-family:%s;font-size:10px;color:%s;padding:2px 6px;border:1px solid %s;border-radius:3px;">esc</span>'
  '</div>'
  '<div style="max-height:400px;overflow:hidden;">%s</div>'
  '<div style="display:flex;align-items:center;gap:12px;padding:8px 13px;border-top:1px solid %s;background:%s;">'
  '<span style="font-family:%s;font-size:10px;color:%s;">↑↓ move</span>'
  '<span style="font-family:%s;font-size:10px;color:%s;">↵ run</span>'
  '<span style="font-family:%s;font-size:10px;color:%s;">⌘k anywhere</span>'
  '<span style="flex-grow:1;"></span>'
  '<span style="font-family:%s;font-size:10px;color:%s;">9 of 64 commands</span></div></div></div>') % (
  SURF, LINE2, LINE, icon('search', 15, TXT3), TXT, SANS, MONO, TXT3, LINE2, _pal_rows,
  LINE, SURF2, MONO, TXT3, MONO, TXT3, MONO, TXT3, MONO, TXT3)

pal_body = ('<div style="position:relative;flex:1 1 0;min-height:0;display:flex;flex-direction:column;">%s%s</div>'
            ) % (ov_body, _palette)

add('Palette.dc.html', 'Command palette', 'Overview',
    pagehead('Overview', 'wed 17 sep · 12:06',
             btn('Run all due', 'ghost', 'play') + btn('New scraper', 'primary', 'plus', 'NewScraper.dc.html')),
    pal_body)

# ============================== 23. MOBILE ==============================
MW, MH = 390, 844
def board_free(inner, w, h):
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&amp;family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap">
<style>
body { margin: 0; background: %(BG)s; font-family: %(SANS)s; -webkit-font-smoothing: antialiased; }
a { color: %(ACC)s; }
a:hover { color: #7BE0DF; }
button { font: inherit; }
</style>
</helmet>
<div style="width:%(W)dpx;height:%(H)dpx;box-sizing:border-box;background:%(BG)s;color:%(TXT)s;font-family:%(SANS)s;display:flex;flex-direction:column;overflow:hidden;">
%(BODY)s
</div>
</x-dc>
<script data-dc-script data-props='{"$preview":{"width":%(W)d,"height":%(H)d}}'>
class Component extends DCLogic {
  renderVals() { return {}; }
}
</script>
</body>
</html>
""" % dict(BG=BG, SANS=SANS, ACC=ACC, TXT=TXT, W=w, H=h, BODY=inner)

_m_items = ''
for name, msg, kind, acts in [
    ('shop-eu-prices','price null on 34% of rows · repair v8 ready','drift','Approve'),
    ('news-archive','blocked · cloudflare challenge','fail','Ack'),
    ('news-paywall','login expired 2 days ago','fail','Ack')]:
    g, c, _ = ST[kind]
    _m_items += ('<a href="#" style="display:flex;flex-direction:column;gap:6px;padding:13px 15px;'
      'border-bottom:1px solid %s;text-decoration:none;">'
      '<div style="display:flex;align-items:center;gap:8px;">'
      '<span style="color:%s;font-family:%s;font-size:12px;">%s</span>'
      '<span style="font-family:%s;font-size:14px;color:%s;font-weight:600;flex-grow:1;">%s</span>'
      '<span style="display:inline-flex;align-items:center;height:30px;padding:0 12px;border:1px solid %s;'
      'border-radius:4px;font-family:%s;font-size:12px;color:%s;">%s</span></div>'
      '<span style="font-family:%s;font-size:12px;color:%s;line-height:1.45;">%s</span></a>') % (
      LINE, c, MONO, g, MONO, TXT, esc(name), LINE2, MONO, TXT2, esc(acts), MONO, TXT3, esc(msg))

_m_tabs = ''
for t, ic, on in [('Alerts','alert',True), ('Runs','play',False), ('Repairs','branch',False), ('More','sliders',False)]:
    _m_tabs += ('<a href="#" style="flex:1 1 0;display:flex;flex-direction:column;align-items:center;'
      'justify-content:center;gap:4px;height:100%%;text-decoration:none;color:%s;">%s'
      '<span style="font-family:%s;font-size:10.5px;">%s</span></a>') % (
      ACC if on else TXT3, icon(ic, 17, ACC if on else TXT3), SANS, esc(t))

mob_inner = ('<header style="height:52px;flex-shrink:0;display:flex;align-items:center;gap:9px;padding:0 15px;'
  'background:%s;border-bottom:1px solid %s;">'
  '<span style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;'
  'border:1.5px solid %s;border-radius:5px;color:%s;">%s</span>'
  '<span style="font-family:%s;font-size:14px;font-weight:600;color:%s;flex-grow:1;">smartscraper</span>'
  '<span style="font-family:%s;font-size:12px;color:%s;">◐ 2</span></header>'
  '<div style="flex:1 1 0;overflow:hidden;display:flex;flex-direction:column;">'
    '<div style="display:flex;gap:9px;padding:13px 15px;flex-shrink:0;">'
      '<div style="flex:1 1 0;background:%s;border:1px solid %s;border-radius:4px;padding:10px 11px;'
      'display:flex;flex-direction:column;gap:3px;">'
      '<span style="font-family:%s;font-size:21px;font-weight:600;color:%s;line-height:1;">3</span>'
      '<span style="font-family:%s;font-size:10.5px;color:%s;">need you</span></div>'
      '<div style="flex:1 1 0;background:%s;border:1px solid %s;border-radius:4px;padding:10px 11px;'
      'display:flex;flex-direction:column;gap:3px;">'
      '<span style="font-family:%s;font-size:21px;font-weight:600;color:%s;line-height:1;">141</span>'
      '<span style="font-family:%s;font-size:10.5px;color:%s;">passed today</span></div>'
      '<div style="flex:1 1 0;background:%s;border:1px solid %s;border-radius:4px;padding:10px 11px;'
      'display:flex;flex-direction:column;gap:3px;">'
      '<span style="font-family:%s;font-size:21px;font-weight:600;color:%s;line-height:1;">$18</span>'
      '<span style="font-family:%s;font-size:10.5px;color:%s;">mtd</span></div></div>'
    '<div style="padding:4px 15px 8px;flex-shrink:0;">%s</div>'
    '<div style="flex:1 1 0;overflow:hidden;">%s</div>'
  '</div>'
  '<nav style="height:62px;flex-shrink:0;display:flex;border-top:1px solid %s;background:%s;">%s</nav>') % (
  SURF, LINE, ACC, ACC, icon('term', 12, ACC, 2), SANS, TXT, MONO, ACC,
  SURF, LINE, MONO, WARN, MONO, TXT3,
  SURF, LINE, MONO, OK, MONO, TXT3,
  SURF, LINE, MONO, TXT, MONO, TXT3,
  label('Needs attention'), _m_items, LINE, SURF, _m_tabs)

BOARDS['Mobile.dc.html'] = ('Phone triage', board_free(mob_inner, MW, MH))

# ---- style guide screen list refresh ----
_SG_NEW = [('Schema & field health','Schema.dc.html'), ('Coverage','Coverage.dc.html'),
           ('MCP console','McpConsole.dc.html'), ('Audit log','Audit.dc.html'),
           ('First run','FirstRun.dc.html'), ('Command palette','Palette.dc.html'),
           ('Phone triage','Mobile.dc.html')]
_t, _doc = BOARDS['StyleGuide.dc.html']
_anchor_tpl = ('<a href="%s" style="display:flex;align-items:center;gap:8px;padding:4px 0;text-decoration:none;">'
               '<span style="font-family:%s;font-size:10px;color:%s;width:16px;">%02d</span>'
               '<span style="font-family:%s;font-size:11px;color:%s;flex-grow:1;">%s</span>%s</a>')
_last = _anchor_tpl % ('StyleGuide.dc.html', MONO, TXT3, 16, MONO, TXT2, 'Style guide', icon('chev', 11, TXT3))
assert _doc.count(_last) == 1
_extra = ''.join(_anchor_tpl % (h, MONO, TXT3, 17 + i, MONO, TXT2, n, icon('chev', 11, TXT3))
                 for i, (n, h) in enumerate(_SG_NEW))
_doc = _doc.replace(_last, _last + _extra)
_cnt = '<span style="font-size:11px;font-family:%s;color:%s;">16</span>' % (MONO, TXT3)
assert _doc.count(_cnt) == 1
_doc = _doc.replace(_cnt, '<span style="font-size:11px;font-family:%s;color:%s;">23</span>' % (MONO, TXT3))
BOARDS['StyleGuide.dc.html'] = (_t, _doc)

# ============================== WRITE ==============================
ORDER = ['Main.dc.html','Palette.dc.html','Scrapers.dc.html','ScraperDetail.dc.html',
         'Runs.dc.html','RunDetail.dc.html','RunLive.dc.html','Records.dc.html',
         'NewScraper.dc.html','BuilderLive.dc.html','Script.dc.html','Schema.dc.html',
         'Repairs.dc.html','RepairReview.dc.html','Audit.dc.html','FirstRun.dc.html',
         'Network.dc.html','Coverage.dc.html','Delivery.dc.html','McpConsole.dc.html',
         'Settings.dc.html','StyleGuide.dc.html','Mobile.dc.html']
COLS = [0, 1520, 3040, 4560]
ROWS = [0, 1260, 2520, 3780, 5040, 6300]
ROWNAMES = ['Daily view', 'Runs and data', 'Authoring', 'Self-healing and trust', 'Infrastructure', 'Foundations']
SIZES = {'Mobile.dc.html': (MW, MH)}

os.makedirs('project', exist_ok=True)
boards = {}
for i, fn in enumerate(ORDER):
    assert fn in BOARDS, fn
    title, doc = BOARDS[fn]
    open(os.path.join('project', fn), 'w', encoding='utf-8').write(doc)
    w, h = SIZES.get(fn, (W, H))
    boards[fn] = {"x": COLS[i % 4], "y": ROWS[i // 4], "w": w, "h": h,
                  "title": title, "is_interactive": True}
notes = {}
for i, nm in enumerate(ROWNAMES):
    notes["row%d" % (i + 1)] = {"x": 0, "y": ROWS[i] - 280, "text": nm, "kind": "title1", "maxW": 6000}
index = {"v": 3, "createdOnFiles": {"v": 1, "at": "2026-09-17T12:10:00Z"},
         "title": "SmartScraper UI", "launch": {"view": "canvas"}, "pages": [],
         "boards": boards, "order": ORDER, "notes": notes, "designSystems": []}
open('project/canvas.json', 'w', encoding='utf-8').write(json.dumps(index, indent=1))
print('wrote %d artboards' % len(ORDER))
