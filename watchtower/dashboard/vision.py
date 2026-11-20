"""Jev Vision: boxes on the agent's screenshots. Display only — it never decides anything and never raises."""

from .. import events

TARGETS = [("the table or list of tenders", "tender list"), ("the link to download the tender documents", "documents")]


async def look(jev, png: bytes, portal_id: str, url: str) -> None:
    boxes = []
    for target, label in TARGETS:
        try:
            b = await jev.locate(png, target, portal_id=portal_id)
        except Exception:
            b = None
        if b is not None:
            boxes.append({"label": label, "top": b.top, "bottom": b.bottom, "left": b.left, "right": b.right,
                          "confidence": b.confidence})
    if boxes:
        events.emit(portal_id, "vision", {"url": url, "boxes": boxes})
