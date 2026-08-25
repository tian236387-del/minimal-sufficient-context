from app.application import create_app
from app.config import Settings
from app.context_compiler import ancestor_path, compile_messages


settings = Settings.from_env()
DB_PATH = settings.database_path
OLLAMA_BASE_URL = settings.ollama_base_url
DEFAULT_MODEL = settings.default_model
DEFAULT_SYSTEM_PROMPT = settings.default_system_prompt

compile_ollama_messages = compile_messages
app = create_app(settings=settings)

