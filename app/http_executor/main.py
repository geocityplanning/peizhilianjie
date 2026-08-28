from .api import create_app
from .service import ExecutionService
from .settings import load_settings
from .store import ExecutorStore


settings = load_settings()
store = ExecutorStore(settings.db_path)
service = ExecutionService(settings, store)
app = create_app(service)

