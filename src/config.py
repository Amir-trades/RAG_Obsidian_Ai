from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    obsidian_vault_path: Path
    default_save_folder: str


def validate_vault_path(raw_path: str | None) -> Path:
    if not raw_path:
        raise ConfigError(
            "OBSIDIAN_VAULT_PATH ist nicht gesetzt. Bitte in .env den Pfad zum "
            "Second-Brain-Vault eintragen."
        )

    vault_path = Path(raw_path).expanduser()
    if not vault_path.exists():
        raise ConfigError(
            f"Obsidian-Vault nicht gefunden: {vault_path}\n"
            "Prüfe, ob Google Drive Desktop gestartet und der Drive gemountet ist."
        )
    if not vault_path.is_dir():
        raise ConfigError(
            f"OBSIDIAN_VAULT_PATH zeigt nicht auf einen Ordner: {vault_path}"
        )
    if not os.access(vault_path, os.R_OK | os.X_OK):
        raise ConfigError(
            f"Obsidian-Vault ist nicht lesbar: {vault_path}\n"
            "Prüfe die macOS-Zugriffsrechte für diesen Ordner."
        )

    try:
        next(vault_path.iterdir(), None)
    except PermissionError as exc:
        raise ConfigError(
            f"Obsidian-Vault kann nicht gelesen werden: {vault_path}\n"
            "Prüfe die macOS-Zugriffsrechte für diesen Ordner."
        ) from exc

    return vault_path


def load_config(env_path: str | Path = ENV_PATH) -> Config:
    values = dotenv_values(Path(env_path))

    gemini_model = values.get("GEMINI_MODEL", "").strip()
    if not gemini_model:
        raise ConfigError("GEMINI_MODEL ist nicht gesetzt.")

    default_save_folder = values.get("DEFAULT_SAVE_FOLDER", "").strip()
    if not default_save_folder:
        raise ConfigError("DEFAULT_SAVE_FOLDER ist nicht gesetzt.")

    return Config(
        gemini_api_key=values.get("GEMINI_API_KEY", "").strip(),
        gemini_model=gemini_model,
        obsidian_vault_path=validate_vault_path(values.get("OBSIDIAN_VAULT_PATH")),
        default_save_folder=default_save_folder,
    )
