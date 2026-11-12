# 管理器在线更新

管理器在本仓库检查工具自身的发行版。「安装管理」中的上游更新入口查询 TotK Recomp 自己的发行版。安装本页的更新 ZIP 只替换管理器文件。内置成品的 0.4.0 和游戏资源 1.4.3 是当前已验证组合，程序会另行检测所选游戏安装。 [上游兼容与版本说明](../README.md#upstream-compatibility-and-updates) · [TotK Recomp 发行版](https://github.com/valentderah/totk-pc-recomp/releases)

## 从 v1.2.8 首次升级

v1.2.8 的程序没有自更新入口。请从 [v1.2.9 Release](https://github.com/Stellarznith/totk-patch-manager-public/releases/tag/v1.2.9) 下载 Windows x64 管理器更新 ZIP 和 `SHA256SUMS.txt`。在 PowerShell 用 `Get-FileHash -Algorithm SHA256 <ZIP 路径>` 核对后，关闭管理器并把 ZIP 中的管理器文件解压到原目录。更新 ZIP 需要现有管理器的 `python/` 和本地组件文件。新用户可以先从源码构建管理器。

## 检查、下载和安装

打开「设置 → 关于与管理器更新」查看当前版本、发行版本、公告、发布日期和可安装包大小。「启动时后台检查管理器更新」默认打开。稳定版读取 GitHub 最新正式 Release。测试版渠道从近期的正式版和预发行版中选择版本号最高的版本。仅源码的 Release 可以查看公告和打开 GitHub 页面。

选择「下载并安装」，查看公告并确认后开始下载。「下载线路」提供自动、仅 GH-Proxy、仅 GitHub 直连。自动模式先尝试 GH-Proxy，镜像失败或哈希不符时尝试 GitHub。版本、下载地址、大小和 SHA-256 由 GitHub 官方 API 提供。镜像只传输 ZIP。

下载和校验结束后，管理器关闭。独立更新进程备份并替换管理器文件，确认新窗口启动后完成更新。下载失败可继续使用当前管理器。替换中断时，重新打开管理器即可继续恢复。更新备份及错误日志位于管理器目录的 `cache/manager-update/`。

## 发布约定

可安装资产名为 `totk-patch-manager-v<版本>-windows-x64-update.zip`，附 `SHA256SUMS.txt`。将 ZIP 作为 GitHub Release 资产上传。正式版使用 `vX.Y.Z` 标签，测试版使用 `vX.Y.Z-beta.1` 等 SemVer 预发行标签，并在 GitHub 标为预发行。

用 `tools/build_manager.py` 构建，再运行 `python tools/make_manager_update.py --app <构建目录> --output <新输出目录>`。工具只读取固定的六个管理器文件及两份许可声明。公开 ZIP 不含游戏数据、图形 DLL、PS5 图案、预编译游戏程序或本地设置。
