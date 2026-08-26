# Alembic — one-time production step

Production's database already has all 8 tables, created the old way (`Base.metadata.create_all()`
on startup, removed in this change). Alembic doesn't know that yet — if you just run
`alembic upgrade head` against production, it will try to `CREATE TABLE roles (...)` etc.
and fail, because those tables already exist.

**Run this ONCE, against production, before deploying this change:**

```bash
export DATABASE_URL="<production External Database URL from Render>"
alembic stamp head
```

`stamp` marks the database as already being at the baseline migration WITHOUT running
any SQL — it just writes a row into a new `alembic_version` table saying "this database
is already at revision 7f0302820c71." No tables are touched, no data is at risk.

**Confirm it worked:**
```bash
python3 -c "
from sqlalchemy import create_engine, text
import os
url = os.environ['DATABASE_URL'].replace('postgres://', 'postgresql://', 1)
engine = create_engine(url)
with engine.connect() as conn:
    result = conn.execute(text('SELECT version_num FROM alembic_version')).fetchone()
    print('stamped at:', result[0])
"
```
Should print `7f0302820c71`.

## From here on, for every future schema change:

1. Edit `app/models.py` as usual.
2. Generate the migration: `alembic revision --autogenerate -m "describe the change"`
3. **Read the generated file in `alembic/versions/`** — autogenerate is good but not
   perfect; check it did what you expect before applying it, especially for column
   deletions or renames (it sometimes reads a rename as "drop old column, add new column,"
   which loses data if applied literally).
4. Apply it locally first: `alembic upgrade head` — confirm it works before touching production.
5. Apply it to production: same command, with `DATABASE_URL` pointed at the production URL.
6. Commit the new file in `alembic/versions/` to git — migrations are part of the codebase,
   not a local artifact. `.gitignore` does NOT exclude this folder.

This replaces the manual `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` workaround used for the
`interviewer_user_id` column — that fix worked, but every future column would have needed
the same manual intervention without this.
