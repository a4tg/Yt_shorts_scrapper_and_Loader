import argparse
import getpass
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from auth_service import hash_password, normalize_email, utc_now
from billing_service import credit_snapshot, grant_credits
from database import check_database, engine
from saas_models import CreditLedger, Job, Payment, User


def create_admin(email_value: str, display_name: str | None) -> int:
    if not check_database():
        print("База или миграции недоступны. Сначала выполни: alembic upgrade head", file=sys.stderr)
        return 1
    try:
        email = normalize_email(email_value)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    password = getpass.getpass("Пароль администратора: ")
    confirmation = getpass.getpass("Повтори пароль: ")
    if password != confirmation:
        print("Пароли не совпадают.", file=sys.stderr)
        return 1
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email)
            db.add(user)
        user.password_hash = password_hash
        user.display_name = (display_name or "").strip()[:120] or user.display_name
        user.status = "active"
        user.is_admin = True
        user.email_verified_at = user.email_verified_at or utc_now()
        db.commit()
        print(f"Администратор готов: {email}")
    return 0


def add_credits(email_value: str, amount: int, reason: str) -> int:
    if not check_database():
        print("База или миграции недоступны. Сначала выполни: alembic upgrade head", file=sys.stderr)
        return 1
    if amount <= 0 or amount > 1_000_000:
        print("Количество должно быть от 1 до 1000000.", file=sys.stderr)
        return 1
    try:
        email = normalize_email(email_value)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    with Session(engine) as db, db.begin():
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            print("Пользователь не найден.", file=sys.stderr)
            return 1
        grant_credits(
            db,
            user.id,
            amount,
            operation_type="admin_grant",
            description=(reason or "Ручное начисление администратором")[:500],
            idempotency_key=f"admin-grant:{uuid.uuid4()}",
        )
        available = credit_snapshot(db, user.id).available
        print(f"Начислено {amount}. Доступный баланс {email}: {available}")
    return 0


def audit_credits(email_value: str | None, *, allow_empty: bool = False) -> int:
    if not check_database():
        print("База или миграции недоступны. Сначала выполни: alembic upgrade head", file=sys.stderr)
        return 1
    with Session(engine) as db:
        statement = select(User).order_by(User.email)
        if email_value:
            try:
                email = normalize_email(email_value)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            statement = statement.where(User.email == email)
        users = db.scalars(statement).all()
        if not users:
            if allow_empty:
                print("Пользователей пока нет; расхождений кредитного журнала нет.")
                return 0
            print("Пользователи не найдены.", file=sys.stderr)
            return 1
        mismatches = 0
        for user in users:
            ledger_balance = int(
                db.scalar(
                    select(func.coalesce(func.sum(CreditLedger.amount), 0)).where(
                        CreditLedger.user_id == user.id
                    )
                )
                or 0
            )
            job_reserve = int(
                db.scalar(
                    select(func.coalesce(func.sum(Job.credits_reserved), 0)).where(
                        Job.user_id == user.id
                    )
                )
                or 0
            )
            valid = ledger_balance == user.credit_balance and job_reserve == user.reserved_credits
            if not valid:
                mismatches += 1
            marker = "OK" if valid else "MISMATCH"
            print(
                f"{marker} {user.email}: balance={user.credit_balance}/{ledger_balance}, "
                f"reserved={user.reserved_credits}/{job_reserve}"
            )
        return 1 if mismatches else 0


def audit_payments() -> int:
    """Check that every successful payment produced exactly one credit grant."""
    if not check_database():
        print("База или миграции недоступны. Сначала выполни: alembic upgrade head", file=sys.stderr)
        return 1
    with Session(engine) as db:
        payments = db.scalars(
            select(Payment).where(Payment.status == "succeeded").order_by(Payment.paid_at)
        ).all()
        mismatches = 0
        for payment in payments:
            ledger_count, ledger_amount = db.execute(
                select(
                    func.count(CreditLedger.id),
                    func.coalesce(func.sum(CreditLedger.amount), 0),
                ).where(
                    CreditLedger.payment_id == payment.id,
                    CreditLedger.operation_type == "subscription_credit",
                )
            ).one()
            valid = (
                bool(payment.provider_payment_id)
                and int(ledger_count) == 1
                and int(ledger_amount or 0) == payment.credits
            )
            if not valid:
                mismatches += 1
            marker = "OK" if valid else "MISMATCH"
            print(
                f"{marker} payment={payment.id} provider={payment.provider_payment_id or '-'} "
                f"credits={payment.credits}/{int(ledger_amount or 0)} grants={int(ledger_count)}"
            )
        print(f"Проверено успешных платежей: {len(payments)}, расхождений: {mismatches}")
        return 1 if mismatches else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Управление пользователями All As Planned")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create-admin", help="Создать или обновить администратора")
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument("--display-name")
    grant_parser = subparsers.add_parser("grant-credits", help="Начислить кредиты пользователю")
    grant_parser.add_argument("--email", required=True)
    grant_parser.add_argument("--amount", required=True, type=int)
    grant_parser.add_argument("--reason", default="Ручное начисление администратором")
    audit_parser = subparsers.add_parser("audit-credits", help="Сверить баланс с журналом")
    audit_parser.add_argument("--email")
    audit_parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Считать пустую базу корректной при первом развёртывании",
    )
    subparsers.add_parser(
        "audit-payments", help="Сверить успешные платежи с начислениями кредитов"
    )
    args = parser.parse_args()
    try:
        if args.command == "create-admin":
            return create_admin(args.email, args.display_name)
        if args.command == "grant-credits":
            return add_credits(args.email, args.amount, args.reason)
        if args.command == "audit-credits":
            return audit_credits(args.email, allow_empty=args.allow_empty)
        if args.command == "audit-payments":
            return audit_payments()
    except OperationalError as exc:
        print(f"Ошибка подключения к базе: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
