"""验证脚本入口：在仓根下运行 `python -m tod_kernel.verify`。"""

from __future__ import annotations

import sys

from tod_kernel.verify import main

if __name__ == "__main__":
    sys.exit(main())
