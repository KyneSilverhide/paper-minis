"""Fold-over paper mini post-processing.

Raw figure (character on a plain white background, black outline) -> cut-out with a black border,
a black base, and a mirrored copy joined at the base for the back side. Figures are then laid out
on A4 sheets with a registration frame, plus a laser-cut SVG in millimetres matching each sheet.
"""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

DPI = 300
MM = 25.4 / DPI  # mm per pixel
A4 = (2480, 3508)
MARGIN_MM = 17
FRAME_LABEL = "Frame: {w:.1f} x {h:.1f} mm (check with a ruler)"


def cut_out(rgb):
    """-> (fg, holes): character silhouette, and enclosed background pockets to fill in black."""
    lo, hi = rgb.min(2).astype(int), rgb.max(2).astype(int)
    light = (lo >= 200) & (hi - lo <= 30)  # white, light grey, fake "transparent" checkerboard
    lab, _ = ndimage.label(light)
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    fg = ~np.isin(lab, edge[edge > 0])
    # enclosed pockets (between the legs, under an arm): only near-perfect background,
    # so white armor or a white beard is never blackened
    holes = np.zeros_like(fg)
    min_area = fg.sum() * 0.001
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None or i in edge:
            continue
        zone = lab[sl] == i
        if zone.sum() < min_area:
            continue
        px = rgb[sl][zone].astype(float)
        if px.mean() >= 245 and px.std(0).max() <= 4:
            holes[sl] |= zone
    return fg, holes


def front(rgb, fg, holes, height_mm, border_mm, base_mm):
    """One side: character + black border + base, head up. -> (RGB uint8, silhouette bool, source px per mm)"""
    ys, xs = np.nonzero(fg)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    # scale from the feet to the top of the head (central band of the body): a raised weapon doesn't shrink the character
    middle = fg[y0 + (y1 - y0) * 2 // 5:y0 + (y1 - y0) * 3 // 5]
    cx = int(np.median(np.nonzero(middle)[1]))
    band = max((x1 - x0) // 10, 1)
    head = np.nonzero(fg[:, max(cx - band, 0):cx + band].any(1))[0].min()
    s = (y1 - head) / height_mm
    r = border_mm * s
    pad = int(r + base_mm * s + 0.25 * (x1 - x0)) + 2
    crop = (slice(y0, y1 + 2 * pad), slice(x0, x1 + 2 * pad))
    img = np.pad(rgb, ((pad, pad), (pad, pad), (0, 0)), constant_values=255)[crop]
    f = ndimage.binary_fill_holes(np.pad(fg, pad)[crop])
    h = np.pad(holes, pad)[crop]

    sil = ndimage.distance_transform_edt(~f) <= r
    lab, n = ndimage.label(sil)
    if n > 1:  # floating particles can't be cut out: keep the character only
        sil = lab == np.argmax(ndimage.sum(sil, lab, range(1, n + 1))) + 1

    # base: black block under the feet, its straight bottom edge is the fold line
    fy, fx = np.nonzero(f)
    sole, top = fy.max(), fy.min()
    fig_h = sole - top
    feet = fx[fy > sole - 0.06 * fig_h]
    bx = (feet.min() + feet.max()) / 2
    width = max((feet.max() - feet.min()) * 1.15 + 2 * r, 0.35 * fig_h)
    width = min(width, 0.9 * (fx.max() - fx.min()))
    base = Image.new("L", (f.shape[1], f.shape[0]), 0)
    b0, b1 = sole - 0.07 * fig_h, sole + max(base_mm * s, r)
    ImageDraw.Draw(base).rounded_rectangle((bx - width / 2, b0, bx + width / 2, b1), radius=(b1 - b0) * 0.5, fill=255, corners=(True, True, False, False))
    sil |= np.array(base) > 0

    visible = f & ~h & sil
    out = np.zeros_like(img)
    out[visible] = img[visible]
    ys, xs = np.nonzero(sil)
    sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    return out[sl], sil[sl], s


def figure(rgb, height_mm=40.0, border_mm=1.0, base_mm=6.0):
    """-> unfolded figure (PIL RGB, 300 dpi, white background): front on top, mirrored back below, joined at the base."""
    fg, holes = cut_out(rgb)
    if not fg.any():
        raise ValueError("No character found: the figure needs a plain white or light background.")
    img, sil, s = front(rgb, fg, holes, height_mm, border_mm, base_mm)
    h, w = sil.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., :3] = img
    rgba[..., 3] = sil * 255
    unfolded = Image.fromarray(np.concatenate([rgba, rgba[::-1]], 0), "RGBA")
    unfolded = Image.alpha_composite(Image.new("RGBA", unfolded.size, (255, 255, 255, 255)), unfolded).convert("RGB")
    k = DPI / 25.4 / s
    return unfolded.resize((max(1, round(unfolded.width * k)), max(1, round(unfolded.height * k))), Image.LANCZOS)


def layout(figs, copies=1, margin_mm=MARGIN_MM, gap_mm=2):
    """Fill A4 pages left to right, top to bottom; a new page starts when one is full."""
    m, g = round(margin_mm / MM), round(gap_mm / MM)
    pages = [Image.new("RGB", A4, "white")]
    x, y, row = m, m, 0
    for f in [f for f in figs for _ in range(copies)]:
        if x + f.width > A4[0] - m and x > m:
            x, y, row = m, y + row + g, 0
        if y + f.height > A4[1] - m and y > m:
            pages.append(Image.new("RGB", A4, "white"))
            x, y, row = m, m, 0
        pages[-1].paste(f, (x, y))
        x += f.width + g
        row = max(row, f.height)
    return pages


def frame(page, label=FRAME_LABEL, inset_mm=0.3, gap_mm=7):
    """Draw a registration frame around the figures and crop the sheet to it.

    -> (cropped sheet, SVG in mm with the same frame, cut contours and fold lines). The cropped sheet is
    smaller than a printer's printable area, so it prints at its true size.
    """
    mask = (np.asarray(page.convert("L")) < 250).astype(np.uint8)
    r = round(inset_mm / MM)  # cut slightly inside the black border: a small misalignment never leaves a white sliver
    mask = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    ys, xs = np.nonzero(mask)
    e, m, t = round(gap_mm / MM), round(2 / MM), round(0.25 / MM)
    x0, y0, x1, y1 = xs.min() - e, ys.min() - e, xs.max() + e, ys.max() + e
    cuts, folds = [], []
    for c in contours:
        c = c - (x0 - m, y0 - m)
        pts = cv2.approxPolyDP(c, 0.5, True)[:, 0] * MM
        cuts.append("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z")
        x, y, w, h = cv2.boundingRect(c)
        yf = y + h // 2  # both sides are symmetric: the fold is in the middle
        row = np.nonzero(mask[yf + y0 - m, x + x0 - m:x + x0 - m + w])[0] + x
        folds.append(f'<line x1="{row.min() * MM:.2f}" y1="{yf * MM:.2f}" x2="{row.max() * MM:.2f}" y2="{yf * MM:.2f}"/>')

    d = ImageDraw.Draw(page)
    d.rectangle((x0 - t // 2, y0 - t // 2, x1 + t // 2, y1 + t // 2), outline="black", width=t)
    d.text((x0 + m, y0 + m), label.format(w=(x1 - x0) * MM, h=(y1 - y0) * MM), fill="black", font=ImageFont.truetype("DejaVuSans.ttf", 32))
    page = page.crop((x0 - m, y0 - m, x1 + m + 1, y1 + m + 1))
    W, H = page.width * MM, page.height * MM
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}mm" height="{H:.2f}mm" viewBox="0 0 {W:.2f} {H:.2f}">
<g id="frame" fill="none" stroke="#00a000" stroke-width="0.1"><rect x="{m * MM:.2f}" y="{m * MM:.2f}" width="{(x1 - x0) * MM:.2f}" height="{(y1 - y0) * MM:.2f}"/></g>
<g id="cut" fill="none" stroke="#ff0000" stroke-width="0.1">{"".join(f'<path d="{p}"/>' for p in cuts)}</g>
<g id="fold" fill="none" stroke="#0000ff" stroke-width="0.1">{"".join(folds)}</g>
</svg>
'''
    return page, svg


def page_fit(fig):
    """Largest scale (1 = as is) at which a figure still fits on a sheet."""
    m = 2 * round(MARGIN_MM / MM)
    return min((A4[0] - m) / fig.width, (A4[1] - m) / fig.height)


def sheets(figs, copies=1, label=FRAME_LABEL):
    """-> [(sheet PIL image, SVG)] one per page."""
    return [frame(p, label) for p in layout(figs, copies)]
