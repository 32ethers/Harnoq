"""Request-local UI language. Domain content and API identifiers are never translated."""
import json
from pathlib import Path
from urllib.parse import urlencode

from flask import g, has_request_context, request

ENGLISH = json.loads((Path(__file__).resolve().parents[1] / "locales" / "en.json").read_text(encoding="utf-8"))
SUPPORTED = ("en", "zh")
DEFAULT_LOCALE = "en"


def gettext(message, *values):
    if has_request_context() and getattr(g, "locale", DEFAULT_LOCALE) == "en":
        message = ENGLISH.get(message, message)
    return message.format(*values) if values else message


def init_i18n(app):
    @app.before_request
    def select_language():
        explicit = request.args.get("lang")
        saved = request.cookies.get("harnoq_locale")
        g.locale = (
            explicit if explicit in SUPPORTED else saved if saved in SUPPORTED
            else DEFAULT_LOCALE
        )

    @app.after_request
    def remember_language(response):
        if request.args.get("lang") in SUPPORTED:
            response.set_cookie("harnoq_locale", g.locale, max_age=365 * 24 * 3600,
                                httponly=True, samesite="Lax", secure=request.is_secure)
        if response.mimetype == "text/html":
            response.headers["Content-Language"] = getattr(g, "locale", DEFAULT_LOCALE)
            response.vary.add("Cookie")
        return response

    @app.context_processor
    def language_context():
        def language_url(language):
            args = request.args.to_dict(flat=False)
            args["lang"] = [language]
            return request.path + "?" + urlencode(args, doseq=True)
        return {"_": gettext, "ui_locale": g.locale,
                "ui_messages": ENGLISH if g.locale == "en" else {},
                "language_url": language_url}
