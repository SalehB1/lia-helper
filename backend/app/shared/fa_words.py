"""Persian stopwords and Liara-domain synonyms used by BM25 tokenization.

Every literal in this module is written in already-normalized form (see
``app.shared.persian.normalize``): Persian ``ی``/``ک``, ASCII digits, no tatweel,
no diacritics, ZWNJ only between letters. Latin terms are lowercase because
``tokenize`` lowercases latin tokens. Note ``آ`` is deliberately spelled ``ا`` here
(``انکه``, ``اپدیت``) because normalization folds it — matching happens post-normalize.

This module must never import ``app.shared.persian`` (that module imports this one).
"""

from __future__ import annotations

# --------------------------------------------------------------------------------------
# Stopwords
# --------------------------------------------------------------------------------------

_PERSIAN_STOPWORDS: tuple[str, ...] = (
    # conjunctions / prepositions / particles
    "و",
    "در",
    "به",
    "از",
    "که",
    "را",
    "با",
    "بر",
    "تا",
    "یا",
    "هم",
    "نیز",
    "اما",
    "ولی",
    "بلکه",
    "یعنی",
    "پس",
    "سپس",
    "چون",
    "زیرا",
    "اگر",
    "وگرنه",
    "بدون",
    "بی",
    "برای",
    "بجز",
    "جز",
    "مگر",
    "ضمن",
    "طی",
    "طبق",
    "توسط",
    "درباره",
    "راجع",
    "نسبت",
    "علیه",
    "همراه",
    "مانند",
    "مثل",
    "همچون",
    "بنابراین",
    "لذا",
    "همچنین",
    "همچنان",
    "البته",
    "حتی",
    "فقط",
    "تنها",
    "صرفا",
    # demonstratives / pronouns
    "این",
    "ان",
    "همین",
    "همان",
    "اینها",
    "انها",
    "اینکه",
    "انکه",
    "انچه",
    "چنین",
    "چنان",
    "من",
    "تو",
    "او",
    "وی",
    "ما",
    "شما",
    "ایشان",
    "خود",
    "خودم",
    "خودت",
    "خودش",
    "خودمان",
    "خودتان",
    "یکدیگر",
    "هرکس",
    "کسی",
    "کس",
    "چیزی",
    "چیز",
    "جایی",
    # question words
    "چه",
    "چرا",
    "چطور",
    "چگونه",
    "کجا",
    "کی",
    "ایا",
    "کدام",
    "چقدر",
    "چند",
    # quantifiers / adverbs
    "هر",
    "همه",
    "برخی",
    "بعضی",
    "بسیار",
    "بسیاری",
    "خیلی",
    "کمی",
    "بیش",
    "بیشتر",
    "کمتر",
    "حدود",
    "تقریبا",
    "کاملا",
    "دقیقا",
    "معمولا",
    "اغلب",
    "همواره",
    "هرگز",
    "دیگر",
    "دیگری",
    "یک",
    "اول",
    "دوم",
    "سوم",
    "قبل",
    "بعد",
    "اکنون",
    "الان",
    "امروز",
    "دیروز",
    "فردا",
    "وقتی",
    "هنگام",
    "هنگامی",
    "زمانی",
    "موقع",
    "اینجا",
    "انجا",
    "بالا",
    "پایین",
    "روی",
    "زیر",
    "کنار",
    "بین",
    "میان",
    "مقابل",
    "سمت",
    "طرف",
    "مورد",
    # verbs / copulas / auxiliaries
    "است",
    "هست",
    "هستم",
    "هستی",
    "هستیم",
    "هستید",
    "هستند",
    "نیست",
    "نیستند",
    "بود",
    "بودم",
    "بودند",
    "بودن",
    "باشد",
    "باشند",
    "باشید",
    "باشم",
    "نباشد",
    "شد",
    "شده",
    "شدند",
    "شدن",
    "شود",
    "شوند",
    "می‌شود",
    "می‌شوند",
    "نمی‌شود",
    "کرد",
    "کردم",
    "کردن",
    "کردند",
    "کرده",
    "کنم",
    "کنی",
    "کنیم",
    "کنید",
    "کنند",
    "می‌کند",
    "می‌کنم",
    "می‌کنید",
    "می‌کنند",
    "دارد",
    "دارم",
    "داریم",
    "دارید",
    "دارند",
    "داشت",
    "داشته",
    "داشتن",
    "ندارد",
    "دهد",
    "دهید",
    "می‌دهد",
    "می‌توان",
    "می‌توانید",
    "می‌توانم",
    "بتوان",
    "توان",
    "باید",
    "نباید",
    "بایست",
    "خواهد",
    "خواهم",
    "خواهیم",
    "خواهند",
    "گرفت",
    "گرفته",
    "رفت",
    "اید",
    "می‌اید",
    "بله",
    "خیر",
    "نه",
    "ها",
    "های",
    "می",
    "ای",
)

# A handful of latin function words: the corpus is bilingual and these add no BM25 signal.
_LATIN_STOPWORDS: tuple[str, ...] = (
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "you",
    "your",
    "are",
    "was",
    "were",
    "will",
    "can",
    "not",
    "but",
    "have",
    "has",
    "its",
    "it",
    "as",
    "at",
    "by",
    "in",
    "of",
    "on",
    "or",
    "to",
    "is",
    "be",
    "an",
    "we",
)

STOPWORDS: frozenset[str] = frozenset(_PERSIAN_STOPWORDS + _LATIN_STOPWORDS)

# --------------------------------------------------------------------------------------
# Synonyms
# --------------------------------------------------------------------------------------

# Each tuple is a set of mutually interchangeable terms; SYNONYMS is derived from it so
# every pair is bidirectional and Persian<->English both ways by construction.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("دیپلوی", "دپلوی", "استقرار", "deploy", "deployment", "انتشار", "منتشر"),
    ("پایگاه‌داده", "دیتابیس", "database", "db", "پایگاه", "داده", "بانک‌اطلاعاتی"),
    ("mysql", "مای‌اس‌کیوال", "mariadb", "ماریادی‌بی"),
    ("postgres", "postgresql", "پستگرس", "psql"),
    ("mongodb", "mongo", "مونگو", "مونگودی‌بی"),
    ("redis", "ردیس"),
    ("elasticsearch", "الستیک", "الستیک‌سرچ"),
    ("rabbitmq", "رابیت", "رابیت‌ام‌کیو"),
    ("دامنه", "domain", "دومین", "دامین"),
    ("ساب‌دامنه", "subdomain", "زیردامنه"),
    ("dns", "دی‌ان‌اس", "nameserver", "نیم‌سرور", "رکورد", "record"),
    ("متغیر", "محیطی", "env", "environment", "variable", "متغیرها", "envs"),
    ("لاگ", "log", "logs", "لاگ‌ها", "گزارش", "رویداد"),
    ("دیسک", "disk", "disks", "volume", "استوریج", "storage", "فضا"),
    ("مخزن", "repository", "repo"),
    ("گیت", "git", "github", "گیت‌هاب", "gitlab", "گیت‌لب"),
    ("اپلیکیشن", "اپ", "app", "application", "برنامه", "apps"),
    ("سرویس", "service", "services"),
    ("هزینه", "تعرفه", "قیمت", "price", "pricing", "cost", "billing", "صورتحساب"),
    ("پلن", "plan", "طرح", "اعتبار", "credit"),
    ("باکت", "bucket", "ابجکت", "object", "ذخیره‌سازی", "s3", "minio"),
    ("ssl", "اس‌اس‌ال", "گواهی", "گواهینامه", "certificate", "cert", "https", "tls"),
    ("letsencrypt", "لتس‌اینکریپت"),
    ("ری‌استارت", "ریستارت", "restart", "reboot", "راه‌اندازی‌مجدد", "reload"),
    ("پورت", "port", "ports", "درگاه"),
    ("بیلد", "build", "ساخت", "compile", "کامپایل", "buildpack", "بیلدپک"),
    ("داکر", "docker", "dockerfile", "کانتینر", "container", "ایمیج", "image"),
    ("nodejs", "node", "نود", "نودجی‌اس", "javascript", "جاوااسکریپت", "npm"),
    ("python", "پایتون", "pip", "wsgi", "gunicorn"),
    ("django", "جنگو"),
    ("flask", "فلسک"),
    ("fastapi", "فست‌ای‌پی‌ای", "uvicorn"),
    ("php", "پی‌اچ‌پی", "composer"),
    ("laravel", "لاراول", "artisan", "ارتیسان"),
    ("nextjs", "next", "نکست", "نکست‌جی‌اس"),
    ("react", "ری‌اکت"),
    ("vue", "ویو", "ویوجی‌اس"),
    ("angular", "انگولار"),
    ("static", "استاتیک", "html", "سایت‌استاتیک"),
    ("go", "golang", "گو", "گولنگ"),
    ("dotnet", "دات‌نت", "csharp", "سی‌شارپ"),
    ("cli", "سی‌ال‌ای", "ترمینال", "terminal", "دستور", "command", "کامند"),
    ("json", "کانفیگ", "config", "configuration", "پیکربندی", "تنظیمات", "settings"),
    ("liara", "لیارا"),
    ("خطا", "error", "ارور", "exception", "اشکال", "مشکل", "issue", "باگ", "bug", "failed"),
    ("عیب‌یابی", "دیباگ", "debug", "troubleshoot", "troubleshooting", "رفع‌خطا"),
    ("نصب", "install", "installation", "setup", "راه‌اندازی"),
    ("اپدیت", "update", "بروزرسانی", "upgrade", "ارتقا"),
    ("حذف", "delete", "remove", "پاک‌کردن", "drop"),
    ("بکاپ", "backup", "پشتیبان", "پشتیبان‌گیری", "snapshot", "اسنپ‌شات"),
    ("بازیابی", "restore", "ریستور"),
    ("مانیتورینگ", "monitoring", "نظارت", "متریک", "metrics"),
    ("رم", "ram", "memory", "حافظه"),
    ("سی‌پی‌یو", "cpu", "پردازنده"),
    ("فایروال", "firewall", "امنیت", "security"),
    ("ip", "ای‌پی", "ایپی", "ادرس‌ای‌پی"),
    ("ایمیل", "email", "mail", "میل", "smtp"),
    ("کرون", "cron", "زمان‌بندی", "scheduler", "کرون‌جاب", "job"),
    ("صف", "queue", "کیو", "worker", "ورکر"),
    ("وب‌سوکت", "websocket", "سوکت", "socket"),
    ("ترافیک", "traffic", "پهنای‌باند", "bandwidth"),
    ("سرور", "server", "ابر", "cloud", "کلاد", "هاست", "host", "hosting", "میزبانی"),
    ("حساب", "account", "اکانت", "کاربری", "user", "کاربر", "پروفایل", "profile"),
    ("توکن", "token", "apikey", "کلید", "key", "api"),
    ("url", "ادرس", "لینک", "link", "نشانی"),
    ("مسیر", "path", "directory", "دایرکتوری", "پوشه", "folder"),
    ("رانتایم", "runtime", "نسخه", "version", "ورژن"),
    ("کش", "cache", "کشینگ", "caching"),
    ("مقیاس", "scale", "scaling", "اسکیل", "replica", "رپلیکا", "instance", "اینستنس"),
    ("هلث‌چک", "healthcheck", "health", "سلامت"),
    ("cicd", "ci", "cd", "pipeline", "پایپلاین", "خودکارسازی"),
    ("زیپ", "zip", "اپلود", "upload", "بارگذاری"),
    ("timeout", "تایم‌اوت", "مهلت"),
)

# Symptom -> concept, ONE WAY ONLY.
#
# Users describe a symptom ("my database gets wiped after every deploy"); the docs describe
# the concept ("disk", "filesystem", "persistence"). BM25 cannot bridge that gap, and it is
# exactly the gap that produces support tickets — measured recall@5 on colloquial
# troubleshooting questions was 2/5 before this table existed.
#
# These are deliberately NOT in _SYNONYM_GROUPS: the mapping must not run backwards. Expanding
# a query for «دیسک» with «پاک» and «خالی» would drag unrelated pages into a question that was
# already precise. Only the symptom side triggers the expansion.
_SYMPTOM_HINTS: dict[str, tuple[str, ...]] = {
    # data vanishing between deploys -> disks & the ephemeral filesystem
    "پاک": ("دیسک", "فایل‌سیستم", "ماندگاری", "disk"),
    "خالی": ("دیسک", "فایل‌سیستم", "disk"),
    "ازدست": ("دیسک", "فایل‌سیستم", "بکاپ", "disk"),
    "میره": ("دیسک", "فایل‌سیستم", "disk"),
    "ریست": ("دیسک", "فایل‌سیستم", "ری‌استارت", "disk"),
    # app will not come up / keeps restarting -> logs, health check, common errors
    "نمیاد": ("لاگ", "هلث‌چک", "خطا", "log"),
    "بالا": ("لاگ", "هلث‌چک", "خطا", "log"),
    "کرش": ("لاگ", "خطا", "ری‌استارت", "log"),
    "استارت": ("لاگ", "هلث‌چک", "ری‌استارت", "restart"),
    "قطع": ("لاگ", "هلث‌چک", "خطا"),
    "بالانمیاد": ("لاگ", "هلث‌چک", "خطا"),
    # slowness / resource pressure -> plans and scaling
    "کند": ("منابع", "پلن", "مقیاس", "رم"),
    "سنگین": ("منابع", "پلن", "رم", "مقیاس"),
    "کمبود": ("منابع", "پلن", "رم"),
    # generic failure words -> troubleshooting surface
    "کارنمیکنه": ("خطا", "لاگ", "عیب‌یابی"),
    "مشکل": ("خطا", "عیب‌یابی", "رفع"),
    "ارور": ("خطا", "error", "عیب‌یابی"),
    "نمیشه": ("خطا", "عیب‌یابی"),
    # connectivity
    "وصل": ("اتصال", "connect", "شبکه"),
    "نمیشناسه": ("اتصال", "dns", "شبکه"),
}


def _build_synonyms(groups: tuple[tuple[str, ...], ...]) -> dict[str, list[str]]:
    """Expand mutually-synonymous groups into a bidirectional term -> alternates map.

    Args:
        groups: Tuples of interchangeable, already-normalized terms.

    Returns:
        Mapping of each term to every other term it appears alongside, order preserved
        and de-duplicated (a term may legitimately belong to more than one group).
    """
    table: dict[str, list[str]] = {}
    for group in groups:
        for term in group:
            alternates = table.setdefault(term, [])
            for other in group:
                if other != term and other not in alternates:
                    alternates.append(other)
    return table


SYNONYMS: dict[str, list[str]] = _build_synonyms(_SYNONYM_GROUPS)

def _merge_symptom_hints(
    table: dict[str, list[str]], hints: dict[str, tuple[str, ...]]
) -> dict[str, list[str]]:
    """Add the one-way symptom hints on top of the bidirectional synonym table.

    A symptom term that also belongs to a synonym group keeps its group alternates and gains
    the concept hints. Nothing is ever added in the reverse direction.

    Args:
        table: The bidirectional table from :func:`_build_synonyms`.
        hints: Symptom term -> concept terms to append.

    Returns:
        The same mapping, mutated in place and returned for convenience.
    """
    for symptom, concepts in hints.items():
        alternates = table.setdefault(symptom, [])
        for concept in concepts:
            if concept not in alternates:
                alternates.append(concept)
    return table


_merge_symptom_hints(SYNONYMS, _SYMPTOM_HINTS)
