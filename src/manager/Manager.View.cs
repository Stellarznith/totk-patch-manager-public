using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public partial class Manager {
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);

    readonly ToolTip tips = new ToolTip { AutoPopDelay = 15000, InitialDelay = 400 };
    readonly Dictionary<string, Button> commands = new Dictionary<string, Button>();
    readonly List<ModernButton> disclosures = new List<ModernButton>();
    readonly List<Font> viewFonts = new List<Font>();
    readonly TextBox settingsPath = new TextBox();
    readonly Label versionRail = new Label(), brand = new Label();
    CheckBox compactNavigation;
    ToggleSwitch controllerOption, videoOption;
    TableLayoutPanel footer;
    Panel shell, rail, workArea;
    ModernButton logButton;
    int activePage;
    bool logOpen, compact;
    float density = 1;
    string bundledVersion, managerVersion;
    Font regular, strong, small, title, section;

    int D(int logical) { return (int)Math.Round(logical * density); }
    Padding Insets(int all) { return new Padding(D(all)); }
    Padding Space(int left, int top, int right, int bottom) {
        return new Padding(D(left), D(top), D(right), D(bottom));
    }
    Font UiFont(float px, FontStyle style = FontStyle.Regular) {
        var font = new Font("Microsoft YaHei UI", px * density, style, GraphicsUnit.Pixel);
        viewFonts.Add(font);
        return font;
    }

    public Manager(int previewDpi = 0) {
        AutoScaleMode = AutoScaleMode.None;
        StartPosition = FormStartPosition.CenterScreen;
        ReadVersions();
        Text = "王国之泪 · 补丁管理器 " + managerVersion;
        density = (previewDpi > 0 ? previewDpi : WindowDpi()) / 96f;
        regular = UiFont(14); strong = UiFont(14, FontStyle.Bold); small = UiFont(12);
        title = UiFont(32, FontStyle.Bold); section = UiFont(17, FontStyle.Bold);
        Font = regular;
        BackColor = Theme.Canvas;
        ForeColor = Theme.Text;
        DoubleBuffered = true;
        var usable = Screen.FromHandle(Handle).WorkingArea;
        var frame = SizeFromClientSize(Size.Empty);
        MinimumSize = SizeFromClientSize(new Size(D(700), D(400)));
        ClientSize = new Size(Math.Min(D(1180), usable.Width - frame.Width),
                              Math.Min(D(755), usable.Height - frame.Height));
        SuspendLayout();
        BuildShell();
        BuildPages();
        BuildFooter();
        ResumeLayout(true);
        ShowPage(0);
        Resize += (s, e) => LayoutRail();
        Shown += async (s, e) => {
            int dark = 1;
            try { DwmSetWindowAttribute(Handle, 20, ref dark, sizeof(int)); }
            catch (DllNotFoundException) { }
            catch (EntryPointNotFoundException) { }
            LoadSettings();
            LayoutRail();
            if (selftest) return;
            if (managerAuto.Checked) BeginInvoke(new Action(async () => await CheckManager(false, true)));
            await DetectGpu();
            if (selected != "") await Inspect();
            if (automatic.Checked) await Check(false);
        };
        FormClosing += (s, e) => {
            if (busy) {
                e.Cancel = true;
                Append("请先取消当前操作，待任务停止后关闭窗口。");
            }
        };
    }

    int WindowDpi() {
        try {
            uint dpi = GetDpiForWindow(Handle);
            if (dpi != 0) return (int)dpi;
        } catch (EntryPointNotFoundException) { }
        using (var graphics = CreateGraphics()) return (int)graphics.DpiX;
    }

    void ReadVersions() {
        bundledVersion = "未识别";
        managerVersion = "1.2.2";
        try {
            string manifest = Path.Combine(app, "prebuilt", "manifest.json");
            if (File.Exists(manifest)) {
                var metadata = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(manifest));
                if (Value(metadata, "version") != "") bundledVersion = Value(metadata, "version");
                if (Value(metadata, "manager_version") != "") managerVersion = Value(metadata, "manager_version");
            }
            string version = Path.Combine(app, "manager-version.txt");
            if (File.Exists(version)) managerVersion = File.ReadAllText(version).Trim();
        } catch (IOException) { }
          catch (ArgumentException) { }
          catch (InvalidOperationException) { }
          catch (UnauthorizedAccessException) { }
    }

    void BuildShell() {
        shell = new Panel { Dock = DockStyle.Fill, Margin = Insets(0) };
        Controls.Add(shell);
        rail = new Panel { Dock = DockStyle.Left, Width = D(208), BackColor = Theme.Rail, Margin = Insets(0) };
        rail.Paint += (s, e) => {
            using (var pen = new Pen(Theme.Border)) e.Graphics.DrawLine(pen, rail.Width - 1, 0, rail.Width - 1, rail.Height);
        };
        var logo = new Emblem { Location = new Point(D(17), D(22)), Size = new Size(D(37), D(37)) };
        rail.Controls.Add(logo);
        brand.Location = new Point(D(66), D(21));
        brand.Size = new Size(D(130), D(43));
        brand.Font = strong;
        brand.ForeColor = Theme.Text;
        brand.Text = "王国之泪\n补丁管理器";
        rail.Controls.Add(brand);
        string[] captions = { "首页", "PS5 手柄", "图形增强", "安装管理", "备份与诊断", "设置", "MOD 管理" };
        Symbol[] glyphs = { Symbol.Home, Symbol.Controller, Symbol.Display, Symbol.Package, Symbol.Shield, Symbol.Settings, Symbol.Package };
        for (int i = 0; i < captions.Length; i++) {
            int page = i;
            var nav = MakeButton(captions[i], glyphs[i], ButtonTone.Navigation, (s, e) => ShowPage(page));
            nav.Location = new Point(D(11), D(91 + i * 48));
            nav.Height = D(42);
            nav.AccessibleDescription = "切换到" + captions[i];
            tips.SetToolTip(nav, captions[i]);
            navigation.Add(nav);
            rail.Controls.Add(nav);
        }
        versionRail.Font = small;
        versionRail.ForeColor = Theme.Faint;
        versionRail.Text = "管理器 " + managerVersion + "\n内置 TotK Recomp " + bundledVersion;
        versionRail.Size = new Size(D(176), D(43));
        rail.Controls.Add(versionRail);
        rail.Resize += (s, e) => LayoutRail();

        var workspace = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1, Margin = Insets(0) };
        workspace.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        workspace.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        shell.Controls.Add(workspace);
        shell.Controls.Add(rail);
        workArea = new Panel { Dock = DockStyle.Fill, BackColor = Theme.Canvas, Margin = Insets(0) };
        workspace.Controls.Add(workArea, 0, 0);
        footer = new TableLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, RowCount = 3, ColumnCount = 1,
            BackColor = Theme.Rail, Margin = Insets(0) };
        footer.RowStyles.Add(new RowStyle(SizeType.Absolute, 0));
        footer.RowStyles.Add(new RowStyle(SizeType.Absolute, D(3)));
        footer.RowStyles.Add(new RowStyle(SizeType.Absolute, D(56)));
        workspace.Controls.Add(footer, 0, 1);
    }

    void LayoutRail() {
        if (rail == null) return;
        bool desired = ClientSize.Width < D(880) || (compactNavigation != null && compactNavigation.Checked);
        compact = desired;
        int width = D(compact ? 70 : 208);
        if (rail.Width != width) rail.Width = width;
        brand.Visible = versionRail.Visible = !compact;
        versionRail.Location = new Point(D(18), Math.Max(D(385), rail.ClientSize.Height - D(56)));
        foreach (ModernButton nav in navigation) {
            nav.Compact = compact;
            nav.Width = Math.Max(D(42), rail.ClientSize.Width - D(22));
            nav.Invalidate();
        }
    }

    void BuildPages() {
        ps5 = new ToggleSwitch(density);
        graphics = new ToggleSwitch(density);
        automatic = new ToggleSwitch(density);
        managerAuto = new ToggleSwitch(density);
        cleanup = new ToggleSwitch(density);
        iconStyle = new DarkCombo(density);
        network = new DarkCombo(density);
        language = new DarkCombo(density);
        managerChannel = new DarkCombo(density);
        MakeCombo(iconStyle, new object[] { "Stellarznith", "Stavaas", "Alerion" }, true);
        MakeCombo(network, new object[] { "自动 · 优先 GH-Proxy，失败后尝试 GitHub", "仅 GH-Proxy 加速（gh-proxy.com）", "仅 GitHub 直连" }, true);
        MakeCombo(language, new object[] { "自动（跟随游戏语言）", "简体中文", "English", "Русский", "Português (Brasil)" }, false);
        MakeCombo(managerChannel, new object[] { "稳定版", "测试版（包含正式版）" }, false);
        managerChannel.SelectedIndexChanged += (s, e) => {
            if (loading) return;
            managerRelease = null;
            managerUpdateStatus.Text = "当前管理器 · " + managerVersion;
            Save();
        };
        MakeToggle(ps5, "安装 PS5 图标与 USB 触觉支持", true, Save);
        MakeToggle(graphics, "安装新版 DLSS 与帧生成", true, Save);
        MakeToggle(automatic, "打开管理器时检查上游更新", false, Save);
        MakeToggle(managerAuto, "启动时后台检查管理器更新", true, Save);
        MakeToggle(cleanup, "同时收纳外置 MOD、注入 DLL 和旧补丁入口", true, null);
        compactNavigation = new ToggleSwitch(density);
        MakeToggle(compactNavigation, "始终使用紧凑图标导航", false, () => { LayoutRail(); Save(); });
        controllerOption = Mirror(ps5, "安装 PS5 图标与 USB 触觉支持");
        videoOption = Mirror(graphics, "安装新版 DLSS 与帧生成");

        var home = Page("王国之泪", "启动游戏、检查补丁状态，或离线安装内置组件。");
        var summary = new ActionRow { BackColor = Theme.Canvas, Margin = Space(0, 0, 0, 15), EqualColumnsFrom = D(600) };
        StatusTile(summary, "游戏程序", current, "待识别", Theme.Text);
        StatusTile(summary, "内置运行时", new Label(), bundledVersion, Theme.Accent);
        StatusTile(summary, "补丁状态", components, "未检查", Theme.Lavender);
        home.Controls.Add(summary);
        Card(home, "游戏位置", null, PathFrame(path), Row(Pick(), Discover(), InspectButton()));
        var start = Card(home, "启动与安装", "安装使用「安装管理」中勾选的组件。",
            Row(Command("launch", "启动游戏", Symbol.Play, ButtonTone.Primary, async () => await Action("launch")),
                Command("home-apply", "安装内置组件", Symbol.Download, ButtonTone.Secondary, async () => await Build("apply")),
                MakeButton("管理组件", Symbol.Package, ButtonTone.Quiet, (s, e) => ShowPage(3))));
        start.BackColor = Theme.Hero;
        foreach (ActionRow row in start.Controls.OfType<ActionRow>()) row.BackColor = start.BackColor;
        Card(home, "上游状态", null, Status(newest, "最新正式版 · 未检查", Theme.Muted),
            Row(Command("home-check", "检查上游更新", Symbol.Refresh, ButtonTone.Secondary, async () => await Check(true))));

        var controller = Page("PS5 手柄", "管理 DualSense 图标样式和 USB 高清触觉组件。");
        Card(controller, "完整手柄支持", null, Status(ps5State, "图标与 USB 触觉 · 未检查", Theme.Muted),
            controllerOption,
            Row(Command("ps5-install", "安装内置 PS5", Symbol.Download, ButtonTone.Primary, async () => await InstallComponent(true)),
                Command("ps5-repair", "检查 / 修复", Symbol.Refresh, ButtonTone.Secondary, async () => await Repair())),
            Hint("三套样式随完整组件一起安装。在游戏 F1 → 操作 → 按键提示选择 PS5，再选择样式并重启。高清触觉使用 USB 连接。"));
        Card(controller, "独立图标", "只替换按键图标时使用，可在游戏的 F1 → 模组中启停。",
            Status(new Label(), "图标样式", Theme.Text), iconStyle,
            Row(Command("icons", "安装 / 修复图标", Symbol.Download, ButtonTone.Secondary, async () => await InstallIcons()),
                Command("icons-disable", "停用独立图标", Symbol.Close, ButtonTone.Quiet, async () => await Action("icons-disable"))));

        var video = Page("图形增强", "安装 DLSS、FSR 和帧生成组件。画质档位与倍率在游戏 F1 中设置。");
        Card(video, "硬件与组件", null, Status(gpu, "显卡 · 正在识别", Theme.Accent),
            Status(graphicsState, "图形组件 · 未检查", Theme.Muted), videoOption,
            Row(Command("graphics-install", "安装内置图形", Symbol.Download, ButtonTone.Primary, async () => await InstallComponent(false)),
                Command("graphics-repair", "检查 / 修复", Symbol.Refresh, ButtonTone.Secondary, async () => await Repair())));
        Card(video, "兼容性与启动排查", "RTX 20 / 30 系列支持 DLSS 超分。NVIDIA 原生 DLSS 帧生成需要相应显卡，AMD FSR 帧生成可用性由运行环境判断。",
            Row(MakeButton("前往备份与诊断", Symbol.Shield, ButtonTone.Secondary, (s, e) => ShowPage(4))));

        var install = Page("安装管理", "选择组件，管理安装、更新、修复和还原。");
        Card(install, "安装内置成品", "内置 " + bundledVersion + " 可离线安装，安装前自动备份游戏程序。",
            ps5, graphics, Row(Command("apply", "安装所选组件", Symbol.Download, ButtonTone.Primary, async () => await Build("apply")),
                Command("repair", "检查 / 修复", Symbol.Refresh, ButtonTone.Secondary, async () => await Repair()),
                Command("check", "检查上游更新", Symbol.Search, ButtonTone.Quiet, async () => await Check(true))));
        Card(install, "回退与还原", "回退按安装记录还原游戏程序。当前存档进度与个人设置继续沿用。",
            Row(Command("rollback", "回退上次更新", Symbol.Refresh, ButtonTone.Secondary, async () => await Action("rollback")),
                Command("disable", "还原安装前游戏", Symbol.Shield, ButtonTone.Secondary, async () => await Action("disable"))));
        Disclosure(install, "高级安装", "当前游戏版本较新时，使用对应的免编译包或按当前版本重编。",
            Row(Command("update", "更新上游并重编", Symbol.Download, ButtonTone.Secondary, async () => await Build("update")),
                Command("rebuild-current", "重编当前版本", Symbol.Refresh, ButtonTone.Secondary, async () => await Build("rebuild-current")),
                Command("prewarm-shaders", "全量预热", Symbol.Display, ButtonTone.Quiet, async () => await Action("prewarm-shaders"))));
        Disclosure(install, "官方还原", "用上游源码重建原版程序。损坏的游戏资源需要原始文件。",
            cleanup, Row(Command("cleanup-scan", "检查多余文件", Symbol.Search, ButtonTone.Secondary, async () => await Action("cleanup-scan")),
                Command("restore-official", "还原官方并重编", Symbol.Shield, ButtonTone.Secondary,
                    async () => await Action("restore-official", new Dictionary<string, object> { { "cleanup", cleanup.Checked } }))));

        var diagnostics = Page("备份与诊断", "备份个人数据，或用安全启动和 SDR 对照定位游戏问题。");
        Card(diagnostics, "启动排查", null,
            Row(Command("launch-safe", "安全启动", Symbol.Shield, ButtonTone.Primary, async () => await Action("launch-safe")),
                Command("launch-sdr", "SDR 排查", Symbol.Display, ButtonTone.Secondary, async () => await Action("launch-sdr"))),
            Hint("安全启动会在本次跳过着色器预热并关闭 DLSS / DLAA、帧生成和 Vulkan 录制线程。SDR 排查会在本次关闭游戏 HDR。"));
        Card(diagnostics, "备份与日志", "导出包包含设置和运行日志，供排查启动、补丁与渲染问题。",
            Row(Command("backup", "备份存档配置", Symbol.Shield, ButtonTone.Secondary, async () => await Action("backup")),
                Track("export", "导出诊断包", Symbol.Log, ButtonTone.Secondary, Export),
                MakeButton("查看操作日志", Symbol.Log, ButtonTone.Quiet, (s, e) => SetLogOpen(true))));
        Disclosure(diagnostics, "缓存与残留", "已识别的渲染缓存、独立图标和扩展设置会收纳到备份。",
            Row(Command("reset-render-cache", "重置渲染缓存", Symbol.Refresh, ButtonTone.Secondary, async () => await Action("reset-render-cache")),
                Command("cleanup-owned", "收纳补丁残留", Symbol.Package, ButtonTone.Secondary, async () => await Action("cleanup-owned"))));

        var settings = Page("设置", "管理游戏路径、菜单语言、下载线路和导航外观。");
        Card(settings, "关于与管理器更新", "从本项目的 GitHub Release 获取版本和公告。",
            Status(managerUpdateStatus, "当前管理器 · " + managerVersion, Theme.Text),
            managerAuto, Status(new Label(), "更新渠道", Theme.Text), managerChannel,
            Row(Command("manager-check", "检查更新", Symbol.Search, ButtonTone.Secondary, async () => await CheckManager(false)),
                Command("manager-update", "下载并安装", Symbol.Download, ButtonTone.Primary, async () => await CheckManager(true)),
                Command("manager-notes", "查看更新公告", Symbol.Log, ButtonTone.Quiet, async () => await ShowManagerNotes()),
                Command("manager-release", "在 GitHub 查看", Symbol.Log, ButtonTone.Quiet, async () => await OpenManagerRelease())));
        Card(settings, "游戏路径", null, PathFrame(settingsPath), Row(Pick(), Discover()));
        var preferences = new ActionRow { BackColor = Theme.Canvas, Margin = Insets(0), EqualColumnsFrom = D(650) };
        var languageCard = Card(settings, "游戏菜单语言", "语言为自动时跟随游戏语言。游戏内 F1 已选择的语言仍优先。",
            language, Row(Command("language", "保存菜单语言", Symbol.Check, ButtonTone.Primary,
                async () => await Action("language", new Dictionary<string, object> { { "language", languages[Math.Max(0, language.SelectedIndex)] } }))));
        var appearanceCard = Card(settings, "外观", "窄窗口自动收起导航文字。", compactNavigation);
        languageCard.Margin = Space(0, 0, 10, 13);
        preferences.Controls.Add(languageCard);
        preferences.Controls.Add(appearanceCard);
        settings.Controls.Add(preferences);
        Card(settings, "下载线路", "管理器更新包和上游资源共用此线路。管理器版本和校验值由 GitHub 提供。",
            network, automatic);
        BuildModCenter();
    }

    PageColumn Page(string heading, string description) {
        var viewport = new Panel { Dock = DockStyle.Fill, AutoScroll = true, BackColor = Theme.Canvas, Visible = false };
        var column = new PageColumn(Theme.Canvas, Color.Empty, 0) { Margin = Insets(0) };
        column.Controls.Add(LabelText(heading, title, Theme.Text, 5));
        column.Controls.Add(LabelText(description, regular, Theme.Muted, 19));
        viewport.Controls.Add(column);
        viewport.Resize += (s, e) => FitPage(viewport, column);
        viewport.VisibleChanged += (s, e) => { if (viewport.Visible) FitPage(viewport, column); };
        workArea.Controls.Add(viewport);
        pages.Add(viewport);
        return column;
    }

    void FitPage(Panel viewport, PageColumn column) {
        int gutter = D(compact ? 18 : 30);
        int usable = viewport.Width - SystemInformation.VerticalScrollBarWidth;
        int width = Math.Min(D(930), usable - 2 * gutter);
        if (width <= D(180)) return;
        Point scroll = viewport.AutoScrollPosition;
        column.Location = new Point(Math.Max(gutter, (usable - width) / 2), D(27) + scroll.Y);
        column.Fit(width);
        viewport.AutoScrollMinSize = new Size(0, column.Height + D(48));
    }

    void ShowPage(int index) {
        activePage = index;
        for (int i = 0; i < pages.Count; i++) {
            pages[i].Visible = i == index;
            var nav = (ModernButton)navigation[i];
            nav.Selected = i == index;
            nav.Invalidate();
        }
        pages[index].BringToFront();
        FitCurrentPage();
        if (!selftest && index == 6 && selected != "" && !busy)
            BeginInvoke(new Action(async () => await RefreshMods()));
    }
    void FitCurrentPage() {
        var viewport = (Panel)pages[activePage];
        FitPage(viewport, (PageColumn)viewport.Controls[0]);
    }

    Label LabelText(string text, Font font, Color color, int bottom) {
        return new Label { Text = text, AutoSize = true, Font = font, ForeColor = color, BackColor = Color.Transparent,
            Margin = Space(0, 0, 0, bottom), UseMnemonic = false };
    }
    Label Status(Label label, string text, Color color) {
        label.Text = text;
        label.AutoSize = true;
        label.Font = strong;
        label.ForeColor = color;
        label.BackColor = Color.Transparent;
        label.Margin = Space(0, 0, 0, 8);
        label.UseMnemonic = false;
        return label;
    }
    Label Hint(string text) { return LabelText(text, small, Theme.Muted, 0); }
    void StatusTile(ActionRow row, string titleText, Label value, string text, Color color) {
        var tile = new PageColumn(Theme.Card, Theme.Border, D(11)) { Padding = Insets(15), Margin = Space(0, 0, 8, 5) };
        tile.Controls.Add(LabelText(titleText, small, Theme.Muted, 5));
        Status(value, text, color);
        value.Margin = Insets(0);
        tile.Controls.Add(value);
        row.Controls.Add(tile);
    }
    PageColumn Card(PageColumn page, string caption, string description, params Control[] contents) {
        var card = new PageColumn(Theme.Card, Theme.Border, D(12)) { Padding = Space(17, 14, 17, 14), Margin = Space(0, 0, 0, 13) };
        if (caption != "") card.Controls.Add(LabelText(caption, section, Theme.Text, description == null ? 11 : 6));
        if (description != null) card.Controls.Add(LabelText(description, regular, Theme.Muted, 12));
        foreach (var child in contents) card.Controls.Add(child);
        page.Controls.Add(card);
        return card;
    }
    void Disclosure(PageColumn page, string caption, string description, params Control[] contents) {
        var details = new PageColumn(Theme.Card, Color.Empty, 0) { Margin = Insets(0), Padding = Space(9, 7, 9, 0), Visible = false };
        details.Controls.Add(LabelText(description, small, Theme.Muted, 9));
        foreach (var item in contents) details.Controls.Add(item);
        var toggle = MakeButton(caption, Symbol.Chevron, ButtonTone.Quiet, null);
        disclosures.Add(toggle);
        toggle.Height = D(35);
        toggle.AccessibleDescription = "展开" + caption;
        toggle.Click += (s, e) => {
            toggle.Expanded = !toggle.Expanded;
            toggle.AccessibleDescription = (toggle.Expanded ? "折叠" : "展开") + caption;
            details.Visible = toggle.Expanded;
            toggle.Invalidate();
            var viewport = (Panel)page.Parent;
            FitPage(viewport, page);
        };
        var card = Card(page, "", null, toggle, details);
        card.Padding = Insets(6);
    }
    ActionRow Row(params Control[] contents) {
        var row = new ActionRow { Margin = Space(0, 2, 0, 0), BackColor = Theme.Card };
        foreach (var item in contents) row.Controls.Add(item);
        return row;
    }
    ModernButton MakeButton(string label, Symbol symbol, ButtonTone tone, EventHandler click) {
        var button = new ModernButton(density) { Text = label, AccessibleName = label, Icon = symbol,
            Tone = tone, Height = D(36), Font = strong, Margin = Space(0, 0, 7, 4) };
        button.Width = TextRenderer.MeasureText(label, button.Font).Width + D(symbol == Symbol.None ? 29 : 50);
        button.Tag = button.Width;
        if (click != null) button.Click += click;
        return button;
    }
    Button Track(string id, string label, Symbol icon, ButtonTone tone, EventHandler click) {
        var button = MakeButton(label, icon, tone, click);
        button.Name = id;
        commands.Add(id, button);
        work.Add(button);
        return button;
    }
    Button Command(string id, string label, Symbol icon, ButtonTone tone, Func<System.Threading.Tasks.Task> action) {
        return Track(id, label, icon, tone, async (s, e) => await action());
    }
    Button Pick() {
        var button = MakeButton("选择游戏", Symbol.Folder, ButtonTone.Secondary, Choose);
        work.Add(button);
        return button;
    }
    Button Discover() {
        var button = MakeButton("自动查找", Symbol.Search, ButtonTone.Quiet, Find);
        work.Add(button);
        return button;
    }
    Button InspectButton() { return Command("inspect", "重新检测", Symbol.Refresh, ButtonTone.Quiet, async () => await Inspect()); }

    PageColumn PathFrame(TextBox input) {
        input.ReadOnly = true;
        input.Font = regular;
        input.BackColor = Theme.Input;
        input.ForeColor = Theme.Text;
        input.BorderStyle = BorderStyle.None;
        input.Margin = Insets(0);
        input.AccessibleName = "游戏程序路径";
        input.GotFocus += (s, e) => {
            RenderPath(input);
            if (selected != "") input.SelectAll();
        };
        input.LostFocus += (s, e) => RenderPath(input);
        input.SizeChanged += (s, e) => { if (!input.Focused) RenderPath(input); };
        var frame = new PageColumn(Theme.Input, Theme.Border, D(8)) { Padding = Space(11, 8, 11, 8), Margin = Space(0, 0, 0, 9) };
        frame.Controls.Add(input);
        return frame;
    }
    void UpdatePath() {
        RenderPath(path);
        RenderPath(settingsPath);
        tips.SetToolTip(path, selected);
        tips.SetToolTip(settingsPath, selected);
    }
    void RenderPath(TextBox input) {
        input.ForeColor = selected == "" ? Theme.Muted : Theme.Text;
        string display = selected == "" ? "尚未选择游戏程序" : selected;
        if (selected != "" && !input.Focused && TextRenderer.MeasureText(display, input.Font).Width > input.Width) {
            int first = display.IndexOfAny(new[] { '\\', '/' }), last = display.LastIndexOfAny(new[] { '\\', '/' });
            if (first >= 0 && first < last)
                display = display.Substring(0, first + 1) + "…" + display.Substring(last);
        }
        input.Text = display;
    }
    void MakeToggle(CheckBox checkbox, string caption, bool value, Action changed) {
        checkbox.Text = caption;
        checkbox.AccessibleName = caption;
        checkbox.Checked = value;
        checkbox.Font = regular;
        checkbox.Margin = Space(0, 0, 0, 5);
        if (changed != null) checkbox.CheckedChanged += (s, e) => changed();
    }
    ToggleSwitch Mirror(CheckBox source, string caption) {
        var mirror = new ToggleSwitch(density);
        MakeToggle(mirror, caption, source.Checked, () => source.Checked = mirror.Checked);
        source.CheckedChanged += (s, e) => mirror.Checked = source.Checked;
        return mirror;
    }
    void MakeCombo(ComboBox combo, object[] items, bool save) {
        combo.DropDownStyle = ComboBoxStyle.DropDownList;
        combo.FlatStyle = FlatStyle.Flat;
        combo.DrawMode = DrawMode.OwnerDrawFixed;
        combo.ItemHeight = D(26);
        combo.Font = regular;
        combo.BackColor = Theme.Input;
        combo.ForeColor = Theme.Text;
        combo.Margin = Space(0, 0, 0, 9);
        combo.Items.AddRange(items);
        combo.SelectedIndex = 0;
        combo.DrawItem += (s, e) => {
            if (e.Index < 0) return;
            bool selectedItem = (e.State & DrawItemState.Selected) != 0;
            using (var brush = new SolidBrush(selectedItem ? Theme.Hover : Theme.Input)) e.Graphics.FillRectangle(brush, e.Bounds);
            var rect = Rectangle.Inflate(e.Bounds, -D(8), 0);
            TextRenderer.DrawText(e.Graphics, combo.GetItemText(combo.Items[e.Index]), combo.Font, rect,
                combo.Enabled ? Theme.Text : Theme.Faint, TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine |
                TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix);
        };
        if (save) combo.SelectedIndexChanged += (s, e) => Save();
    }

    void BuildFooter() {
        log.Dock = DockStyle.Fill;
        log.Multiline = true;
        log.ReadOnly = true;
        log.ScrollBars = ScrollBars.Vertical;
        log.BackColor = Theme.Input;
        log.ForeColor = Theme.Text;
        log.Font = small;
        log.BorderStyle = BorderStyle.None;
        log.Margin = Space(18, 10, 18, 7);
        log.Visible = false;
        log.AccessibleName = "操作日志";
        footer.Controls.Add(log, 0, 0);
        progress.Dock = DockStyle.Fill;
        progress.Margin = Insets(0);
        progress.Visible = false;
        footer.Controls.Add(progress, 0, 1);
        var bar = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, Margin = Insets(0), Padding = Space(17, 7, 17, 6) };
        bar.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        bar.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        bar.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        taskStatus.Dock = DockStyle.Fill;
        taskStatus.Margin = Space(0, 0, 6, 0);
        taskStatus.TextAlign = ContentAlignment.MiddleLeft;
        taskStatus.AutoEllipsis = true;
        taskStatus.Text = lastOutcome;
        taskStatus.ForeColor = Theme.Muted;
        taskStatus.AccessibleName = "当前操作状态";
        bar.Controls.Add(taskStatus, 0, 0);
        logButton = MakeButton("操作日志", Symbol.Log, ButtonTone.Quiet, (s, e) => SetLogOpen(!logOpen));
        logButton.Margin = Space(0, 2, 0, 0);
        bar.Controls.Add(logButton, 1, 0);
        cancel = MakeButton("取消操作", Symbol.Close, ButtonTone.Secondary, (s, e) => CancelOperation());
        cancel.Margin = Space(6, 2, 0, 0);
        cancel.Enabled = cancel.Visible = false;
        bar.Controls.Add(cancel, 2, 0);
        footer.Controls.Add(bar, 0, 2);
    }

    void SetLogOpen(bool open) {
        logOpen = open;
        log.Visible = open;
        footer.RowStyles[0].Height = open ? D(150) : 0;
        logButton.Selected = open;
        logButton.AccessibleDescription = open ? "折叠操作日志" : "展开操作日志";
        logButton.Invalidate();
        footer.PerformLayout();
    }

    protected override void Dispose(bool disposing) {
        base.Dispose(disposing);
        if (disposing) {
            tips.Dispose();
            foreach (var font in viewFonts) font.Dispose();
        }
    }
}
