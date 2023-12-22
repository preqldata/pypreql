from enum import Enum
from typing import List, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from preql.hooks.base_hook import BaseHook
    from preql import Executor, Environment

from preql.dialect.config import DialectConfig

logger = logging.getLogger(__name__)

class Dialects(Enum):
    BIGQUERY = "bigquery"
    SQL_SERVER = "sql_server"
    DUCK_DB = "duck_db"
    PRESTO = "presto"
    TRINO = "trino"
    POSTGRES = "postgres"

    def default_engine(self, conf=None):
        if self == Dialects.BIGQUERY:
            from sqlalchemy import create_engine
            from google.auth import default
            from google.cloud import bigquery

            credentials, project = default()
            client = bigquery.Client(credentials=credentials, project=project)
            return create_engine(
                f"bigquery://{project}?user_supplied_client=True",
                connect_args={"client": client},
            )
        elif self == Dialects.SQL_SERVER:
            from sqlalchemy import create_engine

            raise NotImplementedError()
        elif self == Dialects.DUCK_DB:
            from sqlalchemy import create_engine

            return create_engine(r"duckdb:///:memory:", future=True)
        elif self == Dialects.POSTGRES:
            logger.warn("WARN: Using experimental postgres dialect. Most functionality will not work.")
            import importlib
            spec = importlib.util.find_spec("psycopg2")
            if spec is None:
                raise ImportError(f"postgres driver not installed, installed extra postgres dependencies")
            from sqlalchemy import create_engine
            from preql.dialect.config import PostgresConfig

            if not isinstance(conf, PostgresConfig):
                raise TypeError("Invalid dialect configuration for type postgres")

            return create_engine(conf.connection_string(), future=True)
        else:
            raise ValueError(
                f"Unsupported dialect {self} for default engine creation; create one explicitly."
            )

    def default_executor(
        self, environment: "Environment", 
        hooks: List["BaseHook"] | None = None, conf: DialectConfig | None = None) -> "Executor":
        from preql import Executor

        return Executor(
            engine=self.default_engine(conf=conf),
            environment=environment,
            dialect=self,
            hooks=hooks,
        )
