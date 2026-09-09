"""Shared presentation for durable ticket notices and conversation blocks."""

from .customer_layouts import keyboard
from .keyboards import back_button, callback_button


def notice_markup(ticket_id: int, *, closed: bool = False) -> dict:
    rows = [[callback_button("مشاهده تیکت", f"ticket:{ticket_id}")]]
    if closed:
        rows.append([callback_button("باز کردن مجدد تیکت", f"ticketstatus:{ticket_id}:open", style="success")])
    else:
        rows.extend([
            [{**callback_button("ارسال پاسخ", f"ticketreply:{ticket_id}", style="primary"), "_layout_slot": "reply"}],
            [callback_button("بستن تیکت", f"ticketstatus:{ticket_id}:closed", style="danger")],
        ])
    rows.append([back_button("support")])
    return keyboard("ticket_notice", rows)


def quote_body(rendered: str) -> str:
    # Telegram forbids nesting blockquotes. Rich administrator input may already
    # contain a quote, in which case keep its valid, balanced markup unchanged.
    return rendered if "<blockquote" in rendered else f"<blockquote>{rendered or 'پیوست'}</blockquote>"
