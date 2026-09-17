"""Measure whether every form control on a page is actually reachable.

Not "does it render" but "could a person fill it in": is the control in the
document, does it have a real size, and is it inside the scrollable area rather
than clipped away by an ancestor with overflow:hidden.

Usage:
    python tests/tools/check_small_screen.py <url> [width] [height]
"""

from __future__ import annotations

import asyncio
import sys

PROBE = """
() => {
  const out = [];
  const controls = document.querySelectorAll(
    'input:not([type=hidden]), textarea, select, button, [role=radio], .seg-opt, a.btn'
  );
  const doc = document.documentElement;
  for (const el of controls) {
    // A segmented control hides its radio and shows a label. Measure what a
    // person actually touches, not the input behind it.
    let target = el;
    if ((el.type === 'radio' || el.type === 'checkbox')) {
      const lab = el.closest('label') ||
                  (el.id && document.querySelector(`label[for="${el.id}"]`));
      if (lab) target = lab;
    }
    const r = target.getBoundingClientRect();
    const label =
      el.getAttribute('aria-label') ||
      el.getAttribute('name') ||
      (el.id && (document.querySelector(`label[for="${el.id}"]`)?.textContent || '').trim()) ||
      (el.textContent || '').trim().slice(0, 30) ||
      el.tagName.toLowerCase();

    // Walk up looking for an ancestor that clips this element away.
    let clipped = null;
    for (let p = target.parentElement; p && p !== doc; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflow === 'hidden' || cs.overflowY === 'hidden') {
        const pr = p.getBoundingClientRect();
        if (r.bottom > pr.bottom + 1 || r.top < pr.top - 1) {
          clipped = (p.className || p.tagName).toString().slice(0, 40);
          break;
        }
      }
    }
    out.push({
      label: String(label).slice(0, 40),
      tag: el.tagName.toLowerCase(),
      w: Math.round(r.width),
      h: Math.round(r.height),
      top: Math.round(r.top + window.scrollY),
      clipped,
    });
  }
  return {
    controls: out,
    pageHeight: doc.scrollHeight,
    viewportHeight: window.innerHeight,
    horizontalScroll: doc.scrollWidth > doc.clientWidth + 1,
    scrollWidth: doc.scrollWidth,
    clientWidth: doc.clientWidth,
  };
}
"""


async def main(url: str, width: int, height: int) -> int:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(400)
        data = await page.evaluate(PROBE)
        await browser.close()

    print(f"\nviewport {width}x{height}   page height {data['pageHeight']}px")
    if data["horizontalScroll"]:
        print(f"  HORIZONTAL SCROLL: {data['scrollWidth']} > {data['clientWidth']}")
    print(f"  {len(data['controls'])} controls\n")

    bad = []
    for c in data["controls"]:
        flag = ""
        if c["clipped"]:
            flag = f"CLIPPED by .{c['clipped']}"
            bad.append(c)
        elif c["w"] == 0 or c["h"] == 0:
            flag = "ZERO SIZE"
            bad.append(c)
        print(f"  {c['tag']:<9} {c['label']:<40} {c['w']:>4}x{c['h']:<4} top={c['top']:<6} {flag}")

    print()
    if bad:
        print(f"UNREACHABLE: {len(bad)} of {len(data['controls'])} controls")
        return 1
    if data["horizontalScroll"]:
        print("page scrolls sideways")
        return 1
    print(f"all {len(data['controls'])} controls reachable, no sideways scroll")
    return 0


if __name__ == "__main__":
    u = sys.argv[1]
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 390
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 844
    raise SystemExit(asyncio.run(main(u, w, h)))
