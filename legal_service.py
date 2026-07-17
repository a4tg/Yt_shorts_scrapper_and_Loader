import html
import os
import re
from dataclasses import dataclass


LEGAL_VERSION_DEFAULT = "2026-07-17"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _enabled(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LegalConfig:
    seller_name: str
    seller_inn: str
    seller_address: str
    support_email: str
    version: str
    documents_approved: bool

    @property
    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.seller_name:
            missing.append("seller_name")
        if not re.fullmatch(r"\d{10}|\d{12}", self.seller_inn):
            missing.append("seller_inn")
        if not self.seller_address:
            missing.append("seller_address")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", self.support_email):
            missing.append("support_email")
        if not self.version:
            missing.append("version")
        if not self.documents_approved:
            missing.append("documents_approved")
        return missing

    @property
    def complete(self) -> bool:
        return not self.missing_fields


def legal_config() -> LegalConfig:
    return LegalConfig(
        seller_name=_env("YT_LOADER_LEGAL_SELLER_NAME"),
        seller_inn=_env("YT_LOADER_LEGAL_SELLER_INN"),
        seller_address=_env("YT_LOADER_LEGAL_SELLER_ADDRESS"),
        support_email=_env("YT_LOADER_LEGAL_SUPPORT_EMAIL", "support@allasplanned.ru"),
        version=_env("YT_LOADER_LEGAL_VERSION", LEGAL_VERSION_DEFAULT),
        documents_approved=_enabled("YT_LOADER_LEGAL_DOCUMENTS_APPROVED", default=False),
    )


def legal_acceptance_required() -> bool:
    return _enabled("YT_LOADER_REQUIRE_LEGAL_ACCEPTANCE", default=False)


def commercial_payments_ready(provider_configured: bool, public_url_ready: bool) -> bool:
    return (
        _enabled("YT_LOADER_ENABLE_PAYMENTS", default=False)
        and provider_configured
        and public_url_ready
        and legal_config().complete
    )


PAGE_SECTIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "terms": (
        "Условия использования",
        [
            ("Назначение сервиса", "All As Planned помогает планировать контент, хранить рабочие материалы, согласовывать публикации и обрабатывать медиа."),
            ("Права на материалы", "Пользователь подтверждает наличие прав и законных оснований для загрузки, обработки и публикации материалов."),
            ("Аккаунт и безопасность", "Пользователь отвечает за сохранность данных входа и обязан сообщить поддержке о подозрении на несанкционированный доступ."),
            ("Ограничения", "Запрещены обход тарифных ограничений, вмешательство в работу сервиса, доступ к чужим данным и незаконное использование материалов."),
        ],
    ),
    "offer": (
        "Публичная оферта",
        [
            ("Предмет", "Исполнитель предоставляет удалённый доступ к функциям All As Planned в пределах выбранного тарифа."),
            ("Стоимость и период", "Стоимость, расчётный период, лимиты и условия автопродления показываются до подтверждения оплаты."),
            ("Оплата и автопродление", "Оплата проводится через ЮKassa. Повторные списания выполняются только после отдельного согласия пользователя и могут быть отключены в аккаунте."),
            ("Оказание услуги", "Доступ к платному тарифу предоставляется после подтверждения платежа платёжным провайдером."),
            ("Ответственность", "Стороны отвечают в пределах применимого законодательства; пользователь отвечает за законность загружаемого контента."),
        ],
    ),
    "privacy": (
        "Политика конфиденциальности",
        [
            ("Состав данных", "Сервис обрабатывает данные аккаунта, рабочие материалы, файлы, историю действий, обращения в поддержку и технические журналы."),
            ("Цели обработки", "Данные используются для работы сервиса, защиты аккаунтов, платежей, поддержки, предотвращения злоупотреблений и улучшения качества."),
            ("Передача третьим лицам", "Данные передаются инфраструктурным, почтовым, платёжным и AI-провайдерам только в объёме, необходимом для выбранной функции."),
            ("Права пользователя", "Запрос на доступ, исправление, экспорт или удаление данных направляется на адрес поддержки."),
            ("Безопасность", "Доступ разграничивается по рабочим пространствам; соединения защищаются TLS, а резервные копии шифруются."),
        ],
    ),
    "personal-data-consent": (
        "Согласие на обработку персональных данных",
        [
            ("Согласие", "Создавая аккаунт, пользователь добровольно соглашается на обработку данных, необходимых для регистрации, аутентификации и оказания сервиса."),
            ("Операции", "Обработка может включать сбор, запись, систематизацию, хранение, использование, передачу уполномоченным подрядчикам, блокирование и удаление."),
            ("Срок", "Согласие действует до достижения целей обработки или его отзыва, если дальнейшее хранение не требуется законом."),
            ("Отзыв", "Отзыв согласия направляется на адрес поддержки и может повлечь удаление аккаунта либо невозможность продолжить оказание услуги."),
        ],
    ),
    "refund-policy": (
        "Политика возвратов",
        [
            ("Как запросить возврат", "Запрос направляется в поддержку с email аккаунта, номером платежа и описанием причины."),
            ("Рассмотрение", "Поддержка проверяет платёж, фактическое использование тарифа и обязательные требования законодательства."),
            ("Способ возврата", "Одобренный возврат выполняется через ЮKassa на исходный способ оплаты; срок зачисления зависит от банка."),
            ("Последствия", "При полном возврате начисленные по платежу неиспользованные кредиты отзываются, а соответствующая подписка прекращается."),
        ],
    ),
    "storage-policy": (
        "Политика хранения данных",
        [
            ("Рабочие материалы", "Материалы хранятся в период действия аккаунта и согласно лимитам тарифа, пока пользователь не удалит их."),
            ("Временные результаты", "Исходники и результаты видеообработки могут автоматически удаляться по показанному в интерфейсе таймеру."),
            ("Резервные копии", "Зашифрованные резервные копии хранятся ограниченный срок и удаляются по политике ротации."),
            ("Закрытие аккаунта", "После подтверждённого запроса данные удаляются из активной системы; остаточные копии исчезают по циклу ротации."),
        ],
    ),
}


def render_legal_page(page: str) -> str:
    title, sections = PAGE_SECTIONS[page]
    config = legal_config()
    seller = html.escape(config.seller_name or "Владелец сервиса не указан")
    inn = html.escape(config.seller_inn or "не указан")
    address = html.escape(config.seller_address or "не указан")
    email = html.escape(config.support_email)
    readiness = (
        ""
        if config.complete
        else '<p class="legal-notice">Коммерческие платежи отключены до заполнения и проверки реквизитов владельца.</p>'
    )
    body = "".join(
        f"<h2>{html.escape(heading)}</h2><p>{html.escape(text)}</p>"
        for heading, text in sections
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · All As Planned</title><link rel="stylesheet" href="/assets/landing.css"></head>
<body><main class="landing-section legal"><a href="/">← На главную</a>
<h1>{html.escape(title)}</h1><p>Редакция: {html.escape(config.version)}.</p>{readiness}{body}
<h2>Владелец и контакты</h2><p>{seller}, ИНН {inn}. Адрес: {address}. Email:
<a href="mailto:{email}">{email}</a>.</p>
<p><a href="/offer">Оферта</a> · <a href="/privacy">Конфиденциальность</a> ·
<a href="/personal-data-consent">Согласие на данные</a> · <a href="/refund-policy">Возвраты</a> ·
<a href="/storage-policy">Хранение</a></p></main></body></html>"""
