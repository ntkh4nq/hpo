import os

import psycopg


def connect(**kwargs) -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], **kwargs)
