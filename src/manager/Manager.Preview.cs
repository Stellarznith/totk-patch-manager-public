using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;

public partial class Manager {
    static int PreviewOption(string[] args, string key) {
        int index = Array.IndexOf(args, key), number;
        return index >= 0 && index + 1 < args.Length && int.TryParse(args[index + 1], out number) ? number : 0;
    }

    async Task RunPreview(string[] args) {
        try {
            int width = PreviewOption(args, "--width"), height = PreviewOption(args, "--height");
            if (width > 0 && height > 0) ClientSize = new Size(D(width), D(height));
            if (args[0] == "--selftest-actions") {
                RequireFixture();
                await CheckActionWiring();
            } else if (args[0] == "--selftest-install") {
                RequireFixture();
                await CheckRealInstall();
            } else {
                if (args[0] == "--selftest-local" || args[0] == "--selftest-connected") {
                    await DetectGpu();
                    if (selected != "") await Inspect();
                    if (args[0] == "--selftest-connected") await Check(true);
                }
                await CapturePages();
            }
            File.WriteAllText(Path.Combine(app, "ui-validation.txt"),
                "PASS\nDPI layout: " + (int)Math.Round(density * 96) + "\n" +
                "Client pixels: " + ClientSize.Width + " x " + ClientSize.Height + "\n" +
                "Pages: " + pages.Count + "\nCommands: " + commands.Count + "\n");
        } catch (Exception e) {
            Environment.ExitCode = 1;
            File.WriteAllText(Path.Combine(app, "ui-validation.txt"), e.ToString());
        }
        if (busy) {
            CancelOperation();
            try { await WaitForCommand(); }
            catch (InvalidOperationException) { }
        }
        Close();
    }

    async Task CapturePages() {
        Require(!logOpen && disclosures.All(button => !button.Expanded), "Optional sections start collapsed");
        for (int i = 0; i < pages.Count; i++) {
            navigation[i].PerformClick();
            await Task.Delay(70);
            Require(activePage == i && pages.Count(page => page.Visible) == 1, "Navigation " + i);
            CheckLayout();
            CaptureView("ui-page-" + i);
            if (i == 0) CaptureView("ui-selftest");
        }
        ShowPage(1);
        iconStyle.Focus();
        iconStyle.DroppedDown = true;
        Require(iconStyle.DroppedDown && iconStyle.Items.Count == 3, "PS5 style drop-down opens");
        iconStyle.DroppedDown = false;
        ShowPage(3);
        disclosures[0].PerformClick();
        disclosures[1].PerformClick();
        Require(commands["rebuild-current"].Visible && commands["restore-official"].Visible, "Advanced install opens");
        CheckLayout();
        var viewport = (Panel)pages[activePage];
        viewport.ScrollControlIntoView(commands["restore-official"]);
        CaptureView("ui-advanced");
        ShowPage(4);
        disclosures[2].PerformClick();
        Require(commands["reset-render-cache"].Visible, "Diagnostics details open");
        logButton.PerformClick();
        Require(log.Visible, "Log opens");
        Append("操作日志 · 已完成界面检查");
        CaptureView("ui-log");
        logButton.PerformClick();
        Require(!log.Visible, "Log closes");
    }

    void CheckLayout() {
        var viewport = (Panel)pages[activePage];
        var column = (PageColumn)viewport.Controls[0];
        Require(Math.Abs(rail.Width - D(compact ? 70 : 208)) <= 1,
            "Navigation column width: " + rail.Width + ", compact: " + compact);
        Require(!viewport.HorizontalScroll.Visible, "Horizontal scroll on page " + activePage);
        Require(column.Width > D(180) && column.Height > D(100), "Page content " + activePage);
        foreach (Control root in column.Controls) {
            if (!root.Visible) continue;
            Require(root.Right <= column.Width, "Content exceeds page " + activePage);
            CheckChildren(root);
        }
    }
    void CheckChildren(Control parent) {
        var stack = parent as PageColumn;
        int bottom = stack == null ? 0 : stack.Padding.Top;
        foreach (Control child in parent.Controls) {
            if (!child.Visible) continue;
            if (stack != null) {
                Require(child.Top >= bottom, "Stack overlaps " + child.Text);
                Require(child.Right <= stack.Width - stack.Padding.Right + 1, "Stack overflows " + child.Text);
                bottom = child.Bottom;
            }
            var button = child as ModernButton;
            if (button != null && !button.Compact) {
                int allowance = D(button.Icon == Symbol.None ? 18 : 44);
                Require(TextRenderer.MeasureText(button.Text, button.Font).Width + allowance <= button.Width,
                    "Button label is clipped: " + button.Text);
            }
            CheckChildren(child);
        }
    }
    void CaptureView(string name) {
        Update();
        using (var image = new Bitmap(shell.Width, shell.Height)) {
            shell.DrawToBitmap(image, new Rectangle(Point.Empty, image.Size));
            image.Save(Path.Combine(app, name + ".png"));
        }
    }
    static void Require(bool value, string note) { if (!value) throw new InvalidOperationException(note); }

    void RequireFixture() {
        string expected = Path.Combine(app, "fixture-game", "totk.exe");
        Require(File.Exists(Path.Combine(app, "ui-test-fixture")) &&
            string.Equals(Path.GetFullPath(selected), Path.GetFullPath(expected), StringComparison.OrdinalIgnoreCase),
            "Interactive checks require the fixture game under the test application folder");
    }
    async Task WaitForCommand() {
        var deadline = DateTime.UtcNow.AddMinutes(4);
        while (busy && DateTime.UtcNow < deadline) await Task.Delay(80);
        Require(!busy, "The manager command timed out");
        Require(!lastFailed, "The manager command failed: " + lastOutcome);
    }

    async Task CheckActionWiring() {
        ShowPage(3);
        disclosures[0].PerformClick();
        disclosures[1].PerformClick();
        ShowPage(4);
        disclosures[2].PerformClick();
        foreach (var group in new[] {
            new[] { "launch", "home-apply", "home-check", "inspect" },
            new[] { "ps5-install", "ps5-repair", "icons", "icons-disable" },
            new[] { "graphics-install", "graphics-repair" },
            new[] { "apply", "repair", "check", "rollback", "disable", "update", "rebuild-current", "prewarm-shaders", "cleanup-scan", "restore-official" },
            new[] { "launch-safe", "launch-sdr", "backup", "reset-render-cache", "cleanup-owned" },
            new[] { "language" }
        }.Select((items, page) => new { items, page })) {
            ShowPage(group.page);
            foreach (string id in group.items) {
                Require(commands[id].Visible && commands[id].Enabled, "Action is reachable: " + id);
                commands[id].PerformClick();
                await WaitForCommand();
            }
        }
        compactNavigation.Checked = true;
        Require(compact && Math.Abs(rail.Width - D(70)) <= 1 &&
            navigation.Cast<ModernButton>().All(button => button.Compact), "Compact navigation setting");
        compactNavigation.Checked = false;
    }

    async Task CheckRealInstall() {
        await DetectGpu();
        await Inspect();
        Require(Value(compatibility, "status") == "compatible", "Official fixture is compatible");
        ShowPage(1);
        commands["ps5-install"].PerformClick();
        await Task.Delay(500);
        Require(busy && cancel.Visible && progress.Visible, "Install progress is visible");
        SetLogOpen(true);
        CaptureView("ui-install-in-progress");
        await WaitForCommand();
        Require(Value(lastHealth, "status") == "正常" && Value(lastHealth, "ps5") == "True" &&
            Value(lastHealth, "graphics") == "False", "PS5-only installed");
        SetLogOpen(false);
        CaptureView("ui-install-complete");
        commands["ps5-repair"].PerformClick();
        await WaitForCommand();
        Require(log.Text.Contains("无需重复安装"), "Repeat repair uses installed package");
        ShowPage(3);
        commands["rollback"].PerformClick();
        await WaitForCommand();
        Require(Value(lastHealth, "status") == "未安装", "Rollback restored the original state");
    }
}
