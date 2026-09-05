## هدف و نیازمندی

- شناسه‌های `REQ-*`:
- مسئله بیزنسی/فنی:

## تغییر

- رفتار جدید:
- فایل‌ها/ماژول‌های اصلی:

## ایمنی و سازگاری

- [ ] authorization و ownership بررسی شده است.
- [ ] replay/idempotency/collision بررسی شده است.
- [ ] مسیر خطا و crash recovery بررسی شده است.
- [ ] migration دیتابیس قبلی و rollback، در صورت نیاز، تعریف شده است.
- [ ] هیچ secret، دیتابیس، backup، log یا داده واقعی commit نشده است.

## آزمون

- [ ] `python -m compileall -q app tests`
- [ ] `python -m ruff check .`
- [ ] `python -m unittest discover -s tests -v`
- تست‌های جدید/دستی:

## مستندات و استقرار

- [ ] مستندات و diagramهای متأثر به‌روزرسانی شده‌اند.
- [ ] `docs/TRACEABILITY.md` به‌روزرسانی شده است.
- [ ] rollout، health check و rollback توضیح داده شده است.
