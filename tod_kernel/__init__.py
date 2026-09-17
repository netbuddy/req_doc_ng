"""任务型智能体最小内核（第零步：最小循环）。

模块：
- kernel.py：任务循环、事件流、收发件箱与消息；不导入其他模块。
- tools/：工具包。base（工具、运行工具表、工具规格、共用助手，末尾汇总各模块登记的工具并按任务建运行工具表）、
  dialogue（询问、告知、告知异常）、understanding（对话理解）、patterns（答疑、标记推迟）、
  intake（材料接入登记的三个领域工具）、glossary（生成术语释义）；外部照旧 from tod_kernel.tools import …。
- dialogue.py：话语生成与回答理解，供询问工具调用。
- taskdef.py：任务定义加载器与谓词求值，把 task_defs/ 下的 JSON 任务定义文件读成内核能跑的对象；
  taskdef_step.py：当前步与对话模式（调用选择、记录本步、地址栈、路由、压帧弹帧、帧替换），由 taskdef.py 的任务定义对象继承。
- task_defs/：任务定义数据文件（出差申请单、材料接入登记及其变体与异常样例），文件里只用工具名引用工具。
- observe.py：订阅者（内存收集器、控制台打印、文件订阅者）、重放、运行索引与观测台服务。
- observatory.html：观测台页面（运行索引、运行详情、对比），由 observe.py 的服务提供。
- llm.py：模型调用件。一次模型调用的基础设施，回答来自模型服务或录制文件，由配置文件的「模式」定
  （运行、录制、回放三种）；工具拿到的只是一个调用函数，读不到配置。
- console.py：控制台。宿主一侧的宿主循环与两个应答者（按答案表答的、从键盘取回答的），
  在终端里把使用者与系统的一问一答仿真出来；验证脚本与它共用同一套宿主循环。
- config.example.json：入库的示例配置（模式「回放」）。真实配置 config.json 不入版本库。
- task_defs/recordings/：录制文件，模式「回放」时模型的回答从这里按请求哈希查。

验证脚本：在仓根下运行 `python -m tod_kernel.verify`（包 verify/：base 共用部分，intake、glossary、eval 三组，机器检查子包 machine/ 一组检查一个文件）。
控制台：在仓根下运行 `python -m tod_kernel.console [--config 路径] [--show-calls]`。
观测台：在仓根下运行 `python -m tod_kernel.observe serve [--dir runs] [--port 8765]`，按打印的地址用浏览器打开。
"""
