"""FE 面 Pydantic DTO —— 类名 = OpenAPI schema 名 = 前端生成 TS 类型名。

命名：读 *Read、写命令 *Command、写结果 *Result（docs/40 shared/前端契约适配 §2）。
枚举跨线为稳定码；*Ref 为 str(uuid)。
"""

from .common import *  # noqa: F401,F403
from .projects import *  # noqa: F401,F403
from .materials import *  # noqa: F401,F403
from .elements import *  # noqa: F401,F403
from .item_formation import *  # noqa: F401,F403
from .item_review import *  # noqa: F401,F403
from .publication import *  # noqa: F401,F403
from .assets import *  # noqa: F401,F403
from .templates import *  # noqa: F401,F403
from .charts import *  # noqa: F401,F403
from .runtime_status import *  # noqa: F401,F403
from .notifications import *  # noqa: F401,F403
from .trace import *  # noqa: F401,F403
from .export_readiness import *  # noqa: F401,F403
from .providers import *  # noqa: F401,F403
from .reference_standards import *  # noqa: F401,F403
from .ai_effectiveness import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
