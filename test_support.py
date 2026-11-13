import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path


_test_database_directory = tempfile.mkdtemp(prefix='monteros-tests-')
os.environ['MONTEROS_DATABASE_PATH'] = str(
    Path(_test_database_directory) / 'database.db'
)


def _cleanup_test_database():
    app_module = sys.modules.get('app')
    if app_module is not None:
        with app_module.app.app_context():
            app_module.db.engine.dispose()
    shutil.rmtree(_test_database_directory)


atexit.register(_cleanup_test_database)


def reset_test_database():
    from app import app, db

    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()

    return app, db
