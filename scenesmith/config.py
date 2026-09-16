from dataclasses import dataclass
from typing import Optional
import yaml
from pydantic import BaseModel

@dataclass
class AppConfig:
    search_path: str
    model_adapter: str
    api_key: Optional[str] = None

class AppConfigModel(BaseModel):
    search_path: str
    model_adapter: str
    api_key: Optional[str] = None

def load_config(path: str) -> AppConfig:
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    # Validate with pydantic
    validated = AppConfigModel(**data)
    return AppConfig(**validated.dict())
