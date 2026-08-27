from pathlib import Path

from fastapi.templating import Jinja2Templates

from .router_support.pagination import page_window

_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))
templates.env.globals["page_window"] = page_window
