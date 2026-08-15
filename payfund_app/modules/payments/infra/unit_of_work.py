"""SQLAlchemy transaction boundary for payment orchestration use cases."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from payfund_app.modules.payments.application.errors import PersistenceConflict


class SqlAlchemyUnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session

    def commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise PersistenceConflict() from exc

    def rollback(self) -> None:
        self.session.rollback()
