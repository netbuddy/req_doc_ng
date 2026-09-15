"""任务型智能体最小内核（第零步：最小循环）。

模块：
- kernel.py：任务循环、事件流、收发件箱与消息；不导入其他模块。
- tools.py：工具与工具表，询问工具与按路径写值，以及材料接入登记用的三个领域工具。
- dialogue.py：话语生成与回答理解，供询问工具调用。
- task_travel.py：出差申请单的任务定义；task_intake.py：材料接入登记的任务定义。两者只用工具名引用工具。
- observe.py：订阅者（内存收集器、控制台打印、文件订阅者）、重放、运行索引与观测台服务。
- observatory.html：观测台页面（运行索引、运行详情、对比），由 observe.py 的服务提供。

验证脚本：在仓根下运行 `python -m tod_kernel.verify`。
观测台：在仓根下运行 `python -m tod_kernel.observe serve [--dir runs] [--port 8765]`，按打印的地址用浏览器打开。
"""
