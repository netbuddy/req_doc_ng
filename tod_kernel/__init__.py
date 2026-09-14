"""任务型智能体最小内核（第零步：最小循环）。

四个模块：
- kernel.py：任务循环、事件流、邮箱与消息；不导入另外三个模块。
- tools.py：工具与工具表，登记「询问」工具。
- task_travel.py：出差申请单的任务定义，只用工具名引用工具。
- observe.py：订阅者（内存收集器、控制台打印）与重放。

验证脚本：在仓根下运行 `python -m tod_kernel.verify`。
查看器：在仓根下运行 `python -m tod_kernel.view runs/<文件>.jsonl`，生成同名 HTML 页。
"""
