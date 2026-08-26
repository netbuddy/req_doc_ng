# release/appimage —— Linux 单文件 AppImage（单机模式）

设计依据：req_doc 仓 `docs/proposals/appimage-standalone/AppImage单机模式与图形SVG化方案.md`（本目录与 `release/` 下的 Docker 全量离线包是两条并列的交付路线，脚本不混用）。

## 出包

```bash
cd frontend && npm run build          # 前端产物
release/appimage/build.sh x86_64      # 或 aarch64；可加 --with-font 把出包机的 Noto CJK 字体打进包
```

产物落 `release/appimage/out/ReqDoc-<提交号>-<架构>.AppImage`（约 120 MB）。外部件缓存在 `cache/`：astral 的独立 CPython 3.12（glibc ≥ 2.17）、Temurin 21 JRE、appimagetool 与静态运行时（目标机不需要 libfuse2）。aarch64 可在 x86_64 机器上交叉组装（依赖全是预编译轮子），但验收必须在真 arm64 机器上做。

包内容：独立 Python + 按 `uv.lock` 精确安装的后端依赖、后端源码、前端产物、JRE、`plantuml.jar`。不带 graphviz（见下文「用户须安装 graphviz」）、不带浏览器（Mermaid 由用户浏览器渲染）、不带 LibreOffice（PDF 预览已退役）。

## 运行

```bash
./ReqDoc-*.AppImage                 # 起后端并用系统默认浏览器打开
./ReqDoc-*.AppImage --no-browser    # 只起后端
./ReqDoc-*.AppImage --status        # 看是否在跑
./ReqDoc-*.AppImage --stop          # 停掉
```

数据目录 `REQDOC_HOME`，默认 `~/.local/share/reqdoc/`（`req.db`、`exports/`、`.env`、`server.log`、`runtime.json`）；端口默认 8000，被占用自动顺延（`REQDOC_PORT` 可指定起点）。模型服务地址等配置写在 `REQDOC_HOME/.env`（键名同 `backend/.env.example`）。

## 用户须安装 graphviz

PlantUML 的类图、对象图、组件图等关系型图靠 graphviz 的 `dot` 程序排版。AppImage 不带 graphviz（它不是单个文件，而是一组共享库加插件目录，无法可靠打包），**请在目标机上安装 graphviz**：

```bash
sudo apt install graphviz        # Debian / Ubuntu
sudo dnf install graphviz        # Fedora / RHEL / CentOS
sudo pacman -S graphviz          # Arch
```

后端启动时自动探测 `dot`：装了就用 graphviz 排版；没装时 PlantUML 退回内置的纯 Java 布局引擎 Smetana，图仍能出，但复杂类图的排版会比 graphviz 差一些。时序图、活动图、状态图不经 graphviz，不受影响。

目标机要求：Linux x86_64 或 arm64，glibc ≥ 2.17（CentOS 7 / Ubuntu 16.04 以后），有桌面浏览器。PlantUML 经包内 Java 渲染，Java 量字需要目标机有 fontconfig（`/etc/fonts`，桌面系统必有；裸容器没有）与至少一款字体；中文字体缺失时用 `--with-font` 出的包（多 15 MB，包内带 Noto CJK 并通过 `FONTCONFIG_FILE` 让 fontconfig 同时看到它）。

已验证（2026-08-26）：干净的 ubuntu:22.04 容器（无 libfuse2、无 Python、无 Java）直接运行 `--appimage-extract-and-run` 即就绪；装上 fontconfig 后 PlantUML 出图正常。
