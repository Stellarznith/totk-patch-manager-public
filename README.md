# TotK Patch Manager — Unofficial Add-on for TotK Recomp

**An independent Windows add-on for an existing TotK Recomp installation.** TotK Patch Manager manages controller, graphics and maintenance features. The game port itself is developed and distributed by the TotK Recomp team. [Visit the upstream TotK Recomp repository](https://github.com/valentderah/totk-pc-recomp)

[Download releases](https://github.com/Stellarznith/totk-patch-manager-public/releases) · [Windows build guide](docs/build.md) · [Troubleshooting](docs/faq.md) · [简体中文](#简体中文)

## Features

- **PS5 DualSense** supports three prompt styles, Stellarznith, Stavaas and Alerion, with matching layouts for 15 non-16:9 resolutions. The full controller component provides USB haptics.
- **Graphics** provides NVIDIA DLSS/DLAA and model controls, including manual model M, upstream FSR 1 spatial scaling, and a separate NVIDIA DLSS or AMD FSR frame generation setting. Availability depends on the runtime, GPU, driver and locally supplied libraries.
- **Installation and maintenance** includes version detection, offline installation from a matching local package, repair, backup and rollback, safe launch, shader prewarming and HDR/SDR diagnostics.
- **Manager updates** offers stable and preview channels, release notes, verified downloads, GH-Proxy acceleration with direct GitHub fallback, and rollback if the new manager fails to start.
- **Mod Center** imports ZIPs and folders, manages the same switches and priority as F1, and offers conflict diagnostics, profiles and undo. [Mod Center guide](docs/mod-manager.md)
- **Adaptive patch recipes** merge the extension source into an upstream tree by content, not by line number. A verified recipe is used when the upstream commit matches; otherwise the edits are relocated by their surrounding context inside an isolated workspace, and the merge is repeated for every patched file before anything is written. Edits that the upstream has rewritten, duplicated or overlapped are reported with the file and line and the install stops while the game stays usable.

![TotK Patch Manager Windows GUI showing the DualSense and standalone icon controls](docs/images/manager-controller.png)

## Downloads and installation

1. Set up TotK Recomp with your own lawfully obtained game resources and locate the resulting `totk.exe`. [Upstream project and setup instructions](https://github.com/valentderah/totk-pc-recomp)
2. For a first install, build the manager with the Windows guide. The public **v1.3.4 Windows x64 update ZIP** uses an existing manager installation's Python environment and local components. On v1.2.9 or later, open **Settings → About and manager updates**. For a manual update, verify the ZIP with `SHA256SUMS.txt`. [Build guide](docs/build.md) · [First upgrade and online updates](docs/manager-update.md) · [v1.3.4 Release](https://github.com/Stellarznith/totk-patch-manager-public/releases/tag/v1.3.4)
3. Select your existing `totk.exe` and inspect its detected version. Choose controller, graphics or combined installation if you have a compatible local component bundle. Configure supported options in the game's F1 menu after installation.

The GitHub repository publishes source, documentation and reviewed manager-only updates. Offline installation of all six CPU/component combinations requires a matching local payload. The v1.2.8 GitHub Release contains source archives. [Release audit](docs/release-audit.md) · [v1.2.8 Release](https://github.com/Stellarznith/totk-patch-manager-public/releases/tag/v1.2.8)

## Upstream compatibility and updates

| Version | Meaning |
| --- | --- |
| **Manager version** | v1.3.4 fixes Steam Deck probe initialization order & env var casing, pairs legacy prebuilt packages with verified recipes, improves missing 1.4.3 RomFS & update-ZIP launch messages. v1.3.2 selects and relocates patch recipes by source content. |
| **Detected upstream version** | The manager reads the version of the selected TotK Recomp executable. Use **Installation → Check upstream updates** to query newer upstream releases. |
| **Verified compatibility** | The current offline payload is built from **TotK Recomp 0.4.0**. v1.3.2 also merges the same recipe into a 0.4.0-series tree whose version string, keys or indentation have changed, and refuses the install when an upstream edit collides with a patched line. The audited v1.2.8 package exercised six offline combinations, install/repair/rollback and PS5 prompts at 1920×1080 and 3840×1080. |
| **Game resource version** | **1.4.3** is the verified game resource version for the current offline payload. The manager checks game resources separately against the chosen package or upstream build metadata. |

- **Matching local payload**. The offline workflow checks the current executable, game resources, CPU tier and component manifests. A recognized older build can use an eligible bundled upgrade after those checks pass.
- **Newer or unknown executable**. The manager guards against replacing a newer or unidentified runtime with an older bundled build. Standalone PS5 icons are available when matching local icon assets and a compatible mod loader are present. The icon path leaves the executable in place and has no native haptics.
- **Build against a specific upstream release**. Use **Installation → Advanced → Rebuild current version** when the installed version is recognized, or **Update upstream and rebuild** to explicitly choose the latest upstream release. A compatible patch recipe, upstream Windows kit and validated game inputs are required. The recipe is merged and checked in an isolated workspace first; a recipe that cannot be relocated is reported before the compiler or any game file is touched.
- **Three independent updates**. The manager itself, the upstream program and the patch recipes update separately under **Installation → Three independent updates**. Checking or downloading recipes never changes the manager or the game, and staying on an older upstream is a supported choice.
- **An unsupported upstream version**. A version below every bundled recipe is stopped with a direction instead of a stack trace: the manager offers to upgrade to the latest official upstream and rebuild. The game keeps its working state if that offer is declined.

## Limitations and troubleshooting

DualSense HD haptics use USB. NVIDIA and AMD graphics features have their own hardware and driver requirements. Check render FPS and presented FPS separately when comparing frame generation. The PS5 layouts for other non-16:9 sizes were verified statically in the v1.2.8 audit. See the FAQ for icon fallback, version mismatches, model M, performance and rollback. [FAQ and troubleshooting](docs/faq.md)

## Credits and licensing

Thanks to **Valiantsin Dzerakh (valentderah)** and the TotK Recomp contributors for the upstream port and build tooling. Credit for the supported PS5 styles belongs to their authors, Stellarznith, Stavaas and Alerion. NVIDIA Streamline, AMD FidelityFX and the other upstream dependencies retain their original notices. [Upstream TotK Recomp](https://github.com/valentderah/totk-pc-recomp) · [Third-party notices](THIRD_PARTY_NOTICES.md)

This is an independent, unofficial community project. Nintendo, the TotK Recomp developers and the other relevant vendors are unaffiliated with the project and have not endorsed it. The project does not provide game ROMs, keys, game data or unauthorized game assets. Obtain your own game copy and required resources lawfully, and comply with applicable law and third-party license terms.

Nintendo, The Legend of Zelda and other names and marks belong to their respective rights holders. Original project contributions use the MIT license. Third-party code, artwork and technology retain the terms set by their authors or rights holders. The project's MIT license does not extend to those materials. The software is provided as is, with no guarantee of compatibility across hardware, upstream versions or configurations. Back up personal data before use. [MIT license](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

---

## 简体中文

**王国之泪补丁管理器是为已有 TotK Recomp 安装开发的独立 Windows 扩展工具。** 管理器负责手柄、画面增强与维护功能。游戏移植及其构建工具由 TotK Recomp 上游团队开发和发行。 [访问 TotK Recomp 上游仓库](https://github.com/valentderah/totk-pc-recomp)

### 功能与界面

- **PS5 DualSense**。支持 Stellarznith、Stavaas、Alerion 三套按键样式，为 15 种非 16:9 尺寸提供匹配布局。完整手柄组件可通过 USB 提供高清触觉。
- **图形增强**。支持 NVIDIA DLSS／DLAA、包括手动 M 在内的模型选择、上游 FSR 1 空间放大，以及独立的 NVIDIA DLSS 或 AMD FSR 帧生成。可用性取决于运行时、显卡、驱动和本地库。
- **安装与维护**。支持版本检测、匹配成品的离线安装、修复、备份回滚、安全启动、着色器预热及 HDR／SDR 诊断。
- **管理器更新**。提供稳定版和测试版、更新公告、校验下载、GH-Proxy 加速与 GitHub 直连回退。新版启动失败时可恢复旧版。
- **MOD 管理**。导入 ZIP 和文件夹，使用与 F1 共用的启停状态和优先级，提供冲突诊断、配置方案和撤销。 [使用说明](docs/mod-manager.md)
- **自适应补丁配方**。按源码内容而非行号把扩展合并进上游源码树。上游提交与配方完全一致时直接使用验证配方；否则在隔离工作区里按上下文重新定位每一处编辑，并在写入任何文件之前对全部待改文件重复预检。上游已改写、出现多处或与扩展重叠的编辑会带文件名与行号报出，安装随即停止，游戏保持可用。

### 下载与安装

1. 使用自行合法取得的游戏资源完成 TotK Recomp 安装，找到生成的 `totk.exe`。 [上游安装说明](https://github.com/valentderah/totk-pc-recomp)
2. 首次安装可按构建说明生成管理器。公开的 **v1.3.4 Windows x64 更新 ZIP** 用于升级已有管理器，沿用原安装的 Python 环境及本地组件。v1.2.9 及以上可通过「设置 → 关于与管理器更新」升级。手动更新时请先用 `SHA256SUMS.txt` 校验。 [Windows 构建说明](docs/build.md) · [首次升级与在线更新](docs/manager-update.md) · [v1.3.4 下载](https://github.com/Stellarznith/totk-patch-manager-public/releases/tag/v1.3.4)
3. 在管理器里选择已有 `totk.exe`，查看版本检测结果。有兼容的本地成品时，可选 PS5、图形或整合安装。安装后用游戏内 F1 调整可用功能。

GitHub 仓库提供源码、文档和通过分发审核的管理器更新。六种离线 CPU／组件组合需要匹配的本地成品。v1.2.8 的 GitHub Release 提供源码。 [发布审计](docs/release-audit.md) · [v1.2.8 Release](https://github.com/Stellarznith/totk-patch-manager-public/releases/tag/v1.2.8)

### 上游版本兼容与更新

| 版本 | 含义 |
| --- | --- |
| **工具自身版本** | v1.3.4 修复 Steam Deck 探针初始化顺序与环境变量大小写问题，建立历史预编译包与配方精确配对，并完善 1.4.3 资源缺失与更新包独立运行提示。v1.3.2 按源码内容选择并重定位补丁配方。 |
| **实际识别的上游版本** | 从所选 TotK Recomp 程序中读取。通过「安装管理 → 检查上游更新」查看上游新版本。 |
| **已验证兼容版本** | 当前离线成品基于 **TotK Recomp 0.4.0**。v1.3.2 还能把同一份配方合并进版本号、键名或缩进发生变化但同属 0.4.0 系列的源码树；上游改动与补丁行真正冲突时则拒绝安装。v1.2.8 审计覆盖六种离线组合、安装修复回滚，以及 1920×1080、3840×1080 的 PS5 提示。 |
| **游戏资源版本** | 当前离线成品已验证 **1.4.3**。管理器根据所用成品清单或上游构建元数据单独核验游戏资源。 |

- **本地成品匹配**。离线安装会检查程序版本、游戏资源、CPU 档位及组件清单。符合条件的可识别旧版本也可使用内置成品升级。
- **程序更新或版本未知**。管理器会拦截旧成品覆盖较新或无法识别的程序。有匹配图标资源及兼容 MOD 加载器时，可使用独立图标入口。此入口保留原程序，支持范围为图标。
- **按上游版本本地重编**。版本可识别时，可选「安装管理 → 高级安装 → 重编当前版本」，也可主动选择「更新上游并重编」。需要相应的 Windows kit、可合并的补丁配方及通过校验的游戏资源。配方会先在隔离工作区完成合并与校验；无法重定位的配方在下载编译器、改动任何游戏文件之前即报出。
- **三种更新各自独立**。「安装管理 → 三种更新各自独立」把管理器本身、上游程序、适配配方分开更新。检查或下载配方不会改动管理器或游戏，留在旧上游也是受支持的选择。
- **上游版本不受支持**。低于本机所有配方的版本会给出可执行的方向而不是堆栈信息：管理器提供「一键升级上游并安装」，把上游升到官方最新正式版再重编。选择不升级时游戏保持原有可用状态。

### 使用限制与常见问题

DualSense 高清触觉使用 USB。NVIDIA 与 AMD 的图形功能各有硬件和驱动要求。比较帧生成效果时应分别查看基础帧率和呈现帧率。v1.2.8 的其他非 16:9 尺寸通过了静态资源检查。按键样式回退、版本不匹配、模型 M、性能和回滚操作见故障排查。 [常见问题](docs/faq.md)

### 鸣谢、免责声明与许可

感谢 **Valiantsin Dzerakh（valentderah）** 和 TotK Recomp 贡献者开发上游移植与构建工具。三套 PS5 样式的美术署名归各自作者 Stellarznith、Stavaas 和 Alerion。NVIDIA Streamline、AMD FidelityFX 和其他依赖保留原有许可及署名。 [上游 TotK Recomp](https://github.com/valentderah/totk-pc-recomp) · [第三方声明](THIRD_PARTY_NOTICES.md)

本项目为独立、非官方的社区工具，与 Nintendo、TotK Recomp 上游开发团队及其他相关厂商没有官方关联，亦未获其认可。本项目不提供游戏 ROM、密钥、游戏数据或未经授权的游戏资源。用户须自行合法取得所需游戏副本及资源，并遵守适用法律和第三方许可。

Nintendo、The Legend of Zelda 等名称及商标归各自权利人所有。项目原创贡献采用 MIT 许可证。第三方代码、美术和技术组件分别遵循原作者或权利人的许可，本项目的 MIT 许可证不自动覆盖这些内容。软件按现状提供，无法保证适用于所有硬件、上游版本与配置。使用前建议备份个人数据。 [MIT 许可证](LICENSE) · [第三方声明](THIRD_PARTY_NOTICES.md)
