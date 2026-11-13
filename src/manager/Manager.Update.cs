using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public partial class Manager {
    readonly Label managerUpdateStatus = new Label();
    readonly Label managerRecipeStatus = new Label();
    ToggleSwitch managerAuto;
    ComboBox managerChannel;
    Dictionary<string, object> managerRelease;
    int updateRequest;

    string ManagerChannel() { return managerChannel.SelectedIndex == 1 ? "preview" : "stable"; }

    async Task CheckManager(bool install, bool background = false) {
        string channel = ManagerChannel();
        int id = ++updateRequest;
        var release = await Execute("manager-check", new Dictionary<string, object> { { "channel", channel } }, background);
        if (IsDisposed || id != updateRequest || channel != ManagerChannel()) return;
        managerRelease = release;
        if (managerRelease == null) {
            managerUpdateStatus.Text = "检查失败，请查看操作日志";
            FitCurrentPage();
            return;
        }
        string version = Value(managerRelease, "version"), status = Value(managerRelease, "status");
        managerUpdateStatus.Text = ReleaseSummary(managerRelease);
        FitCurrentPage();
        bool newerSource = status == "source-only" && Value(managerRelease, "newer") == "True";
        if (background && !busy && (status == "available" || newerSource)) {
            lastOutcome = "管理器 v" + version + (newerSource ? " 已发布源码" : " 可更新") + " · 设置";
            UpdateTaskStatus(lastOutcome, Theme.Accent);
        }
        if (!install || status != "available" || !ManagerNotes(managerRelease, true)) return;
        if (!String.Equals(Path.GetFileName(Application.ExecutablePath), "王国之泪补丁管理器.exe", StringComparison.OrdinalIgnoreCase)) {
            MessageBox.Show(this, "请使用原文件名「王国之泪补丁管理器.exe」启动后更新。", "管理器更新");
            return;
        }
        var result = await Execute("manager-update", new Dictionary<string, object> {
            { "version", version }, { "channel", channel }, { "sha256", Value(managerRelease, "sha256") }
        });
        if (result == null) return;
        try {
            StartUpdateWorker(app);
            Close();
        } catch (Exception e) {
            Append("无法启动更新进程：" + e.Message);
            MessageBox.Show(this, "更新包已就绪。请关闭窗口后重新打开管理器以继续更新。\n" + e.Message, "管理器更新");
        }
    }

    static IEnumerable<object> RecipeRows(Dictionary<string, object> data, string key) {
        object value;
        if (data == null || !data.TryGetValue(key, out value) || value == null) yield break;
        var array = value as object[];
        if (array != null) { foreach (var item in array) yield return item; yield break; }
        var list = value as System.Collections.IEnumerable;
        if (list == null || value is string) yield break;
        foreach (var item in list) yield return item;
    }

    async Task CheckRecipes(bool install) {
        var result = await Execute(install ? "recipe-sync" : "recipe-check");
        if (IsDisposed || result == null) {
            managerRecipeStatus.Text = install ? "配方更新失败，请查看操作日志" : "配方检查失败，请查看操作日志";
            FitCurrentPage();
            return;
        }
        var names = new List<string>();
        foreach (var item in RecipeRows(result, "local")) {
            var row = item as Dictionary<string, object>;
            if (row != null) names.Add(Value(row, "basis"));
        }
        names.Sort(StringComparer.Ordinal);
        string summary = "本机适配配方：" + (names.Count == 0 ? "无" : String.Join("、", names.ToArray()));
        var newer = new List<string>();
        foreach (var item in RecipeRows(result, "pending")) newer.Add(Convert.ToString(item));
        if (newer.Count > 0) summary += " · 可下载：" + String.Join("、", newer.ToArray());
        managerRecipeStatus.Text = summary;
        FitCurrentPage();
        if (install) Append(summary);
    }

    async Task ShowManagerNotes() {
        if (managerRelease == null) await CheckManager(false);
        if (managerRelease != null) ManagerNotes(managerRelease, false);
    }

    async Task OpenManagerRelease() {
        if (managerRelease == null) await CheckManager(false);
        string url = Value(managerRelease, "url");
        if (!url.StartsWith("https://github.com/Stellarznith/totk-patch-manager-public/releases/tag/v",
                            StringComparison.Ordinal)) return;
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch (Exception e) { Append("无法打开 GitHub：" + e.Message); }
    }

    string ReleaseSummary(Dictionary<string, object> release) {
        string status = Value(release, "status");
        string summary = "当前 " + managerVersion + " · GitHub " + Value(release, "version") +
            (status == "available" ? " · 可更新" : status == "source-only" ? " · 仅提供源码" : " · 无可安装的新版本");
        DateTimeOffset published;
        if (DateTimeOffset.TryParse(Value(release, "published"), CultureInfo.InvariantCulture, DateTimeStyles.None, out published))
            summary += " · " + published.ToLocalTime().ToString("yyyy-MM-dd HH:mm");
        long bytes;
        if (Int64.TryParse(Value(release, "size"), out bytes))
            summary += " · " + (bytes < 1024 * 1024 ? (bytes / 1024.0).ToString("F1") + " KB" :
                (bytes / (1024.0 * 1024.0)).ToString("F1") + " MB");
        return summary;
    }

    bool ManagerNotes(Dictionary<string, object> release, bool install) {
        string notes = Value(release, "notes");
        using (var dialog = new Form {
            Text = "更新公告 · v" + Value(release, "version"),
            ClientSize = new Size(D(600), D(425)), MinimumSize = new Size(D(420), D(300)),
            StartPosition = FormStartPosition.CenterParent, Font = regular, BackColor = Theme.Card,
            MinimizeBox = false, MaximizeBox = false, ShowInTaskbar = false
        }) {
            var body = new TextBox {
                Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
                BackColor = Theme.Card, ForeColor = Theme.Text, BorderStyle = BorderStyle.FixedSingle,
                Text = ReleaseSummary(release) + Environment.NewLine + Environment.NewLine +
                    (notes == "" ? "该发行版暂无更新公告。" : notes.Replace("\r\n", "\n").Replace("\n", Environment.NewLine))
            };
            var actions = new FlowLayoutPanel {
                Dock = DockStyle.Bottom, Height = D(51), FlowDirection = FlowDirection.RightToLeft,
                Padding = Space(6, 6, 6, 0)
            };
            var close = MakeButton(install ? "取消" : "关闭", Symbol.Close, ButtonTone.Secondary, (s, e) => dialog.DialogResult = DialogResult.Cancel);
            close.DialogResult = DialogResult.Cancel;
            dialog.CancelButton = close;
            actions.Controls.Add(close);
            if (install)
                actions.Controls.Add(MakeButton("下载并更新", Symbol.Download, ButtonTone.Primary, (s, e) => dialog.DialogResult = DialogResult.OK));
            dialog.Controls.Add(body);
            dialog.Controls.Add(actions);
            dialog.ActiveControl = body;
            return dialog.ShowDialog(this) == DialogResult.OK;
        }
    }

    static string TrialToken(string[] args, string trial) {
        Guid parsed;
        if (args.Length != 2 || args[0] != "--updated-manager" || !Guid.TryParseExact(args[1], "N", out parsed)
            || !File.Exists(trial)) return "";
        try {
            var record = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(trial));
            object token;
            return record.TryGetValue("token", out token) && Convert.ToString(token) == args[1] ? args[1] : "";
        } catch (Exception) { return ""; }
    }

    static void ConfirmManagerStartup(Manager window, string update, string token) {
        try { File.WriteAllText(Path.Combine(update, "startup-ok.txt"), token, Encoding.ASCII); }
        catch (Exception e) { window.Append("无法确认新版本启动：" + e.Message); window.Close(); }
    }

    static void StartUpdateWorker(string folder) {
        var worker = Path.Combine(folder, "cache", "manager-update", "worker", "manager_update.py");
        if (!File.Exists(worker)) throw new IOException("找不到已暂存的更新程序。");
        var start = new ProcessStartInfo(Path.Combine(folder, "python", "python.exe"),
            "-X utf8 \"" + worker + "\" " + Process.GetCurrentProcess().Id) {
            WorkingDirectory = folder, UseShellExecute = false, CreateNoWindow = true
        };
        using (var process = Process.Start(start)) {
            if (process == null) throw new IOException("更新进程启动失败。");
        }
    }
}
