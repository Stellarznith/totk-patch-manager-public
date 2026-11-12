using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// The UI passes JSON files to the bundled backend; paths never become shell expressions.
public partial class Manager : Form {
    static bool selftest;
    readonly string app = AppDomain.CurrentDomain.BaseDirectory;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly TextBox path = new TextBox(), log = new TextBox();
    readonly Label current = new Label(), newest = new Label(), components = new Label(), gpu = new Label();
    readonly Label ps5State = new Label(), graphicsState = new Label();
    readonly List<Control> pages = new List<Control>();
    readonly List<Button> navigation = new List<Button>();
    CheckBox ps5, graphics, automatic, cleanup;
    ComboBox network, language, iconStyle;
    readonly string[] iconStyles={"white","stavaas","alerion"};
    readonly string[] languages={"auto","zh-Hans","en","ru","pt-BR"};
    readonly List<Button> work = new List<Button>();
    readonly TaskProgress progress = new TaskProgress();
    readonly Label taskStatus = new Label();
    Button cancel;
    bool busy, loading;
    string selected = "", latest = "", cancelFile = "";
    string lastOutcome = "就绪";
    bool lastFailed;
    Dictionary<string, object> lastHealth, compatibility;
    string gameVersion = "";

    void Append(string message) {if(IsDisposed)return;if(InvokeRequired){BeginInvoke(new Action<string>(Append),message);return;}if(log.TextLength>400000)log.Text=log.Text.Substring(log.TextLength-250000);log.AppendText(message+Environment.NewLine);}
    void SetBusy(bool value) {
        busy = value;
        foreach (var button in work) button.Enabled = !value;
        ps5.Enabled = graphics.Enabled = controllerOption.Enabled = videoOption.Enabled =
            automatic.Enabled = managerAuto.Enabled = managerChannel.Enabled = network.Enabled =
            language.Enabled = iconStyle.Enabled = cleanup.Enabled = !value;
        cancel.Enabled = cancel.Visible = value;
        progress.Visible = value;
        progress.Style = ProgressBarStyle.Marquee;
        UpdateTaskStatus(value ? "正在处理…" : lastOutcome, !value && lastFailed ? Theme.Warning : value ? Theme.Accent : Theme.Muted);
    }
    void UpdateTaskStatus(string text, Color color) {
        taskStatus.Text = text;
        taskStatus.ForeColor = color;
        tips.SetToolTip(taskStatus, text);
    }
    void CancelOperation() {
        if (!busy || cancelFile == "") return;
        try {
            File.WriteAllText(cancelFile, "cancel");
            cancel.Enabled = false;
            UpdateTaskStatus("正在取消，等待当前步骤结束", Theme.Accent);
        } catch (IOException e) {
            Append("无法写入取消请求：" + e.Message);
        } catch (UnauthorizedAccessException e) {
            Append("无法写入取消请求：" + e.Message);
        }
    }
    string NetworkMode() {return network.SelectedIndex==1?"mirror":network.SelectedIndex==2?"direct":"auto";}
    string StyleName(string value) {return value=="stavaas"?"Stavaas":value=="alerion"?"Alerion":value=="white"?"Stellarznith":value==""?"未安装":"未知";}
    Dictionary<string,object> Dict(object value) {return value as Dictionary<string,object>;}
    string Value(Dictionary<string,object> d,string key) {return d!=null&&d.ContainsKey(key)?Convert.ToString(d[key]):"";}
    void LoadSettings() {
        loading=true;
        try {
            var p = Path.Combine(app, "settings.json");
            if (!File.Exists(p)) return;
            var s = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(p));
            selected = Value(s, "game");
            ps5.Checked = !s.ContainsKey("ps5") || Convert.ToBoolean(s["ps5"]);
            graphics.Checked = !s.ContainsKey("graphics") || Convert.ToBoolean(s["graphics"]);
            automatic.Checked = s.ContainsKey("auto") && Convert.ToBoolean(s["auto"]);
            managerAuto.Checked = !s.ContainsKey("manager_auto") || Convert.ToBoolean(s["manager_auto"]);
            managerChannel.SelectedIndex = Value(s, "manager_channel") == "preview" ? 1 : 0;
            compactNavigation.Checked = s.ContainsKey("compact_navigation") && Convert.ToBoolean(s["compact_navigation"]);
            string route = Value(s, "network");
            network.SelectedIndex = route == "mirror" ? 1 : route == "direct" ? 2 : 0;
            iconStyle.SelectedIndex = Math.Max(0, Array.IndexOf(iconStyles, Value(s, "style")));
        } catch (Exception e) { Append("设置读取失败：" + e.Message); }
        finally { loading = false; UpdatePath(); }
    }
    void Save() {
        if (loading) return;
        try {
            string file = Path.Combine(app, "settings.json");
            var values = File.Exists(file) ? json.Deserialize<Dictionary<string, object>>(File.ReadAllText(file))
                                           : new Dictionary<string, object>();
            values["game"] = selected;
            values["ps5"] = ps5.Checked;
            values["graphics"] = graphics.Checked;
            values["auto"] = automatic.Checked;
            values["manager_auto"] = managerAuto.Checked;
            values["manager_channel"] = ManagerChannel();
            values["network"] = NetworkMode();
            values["style"] = iconStyles[Math.Max(0, iconStyle.SelectedIndex)];
            values["compact_navigation"] = compactNavigation.Checked;
            string pending = file + ".new";
            File.WriteAllText(pending, json.Serialize(values), new UTF8Encoding(false));
            if (File.Exists(file)) File.Replace(pending, file, null);
            else File.Move(pending, file);
        } catch (Exception e) { Append("设置保存失败：" + e.Message); }
    }
    async Task<Dictionary<string,object>> Execute(string action,Dictionary<string,object> extra=null,bool background=false) {
        if(busy&&!background)return null;
        if(action!="check"&&action!="discover"&&action!="manager-check"&&action!="manager-update"&&selected==""){Append("请先选择游戏。");UpdateTaskStatus("请先选择游戏程序", Theme.Warning);return null;}
        if (!background) SetBusy(true);
        Dictionary<string,object> result=null;bool success=false;string failure="";
        try {
            var id = Guid.NewGuid().ToString("N");
            var requests = Path.Combine(app, "cache", "requests");
            Directory.CreateDirectory(requests);
            var request = new Dictionary<string, object> { { "action", action }, { "game", selected },
                { "ps5", ps5.Checked }, { "graphics", graphics.Checked }, { "network", NetworkMode() }, { "cache", Path.Combine(app, "cache") } };
            if (background) request["cache"] = Path.Combine(app, "cache", "manager-update", "check");
            else {
                cancelFile = Path.Combine(requests, id + ".cancel");
                request["cancel"] = cancelFile;
            }
            if (extra != null) foreach (var pair in extra) request[pair.Key] = pair.Value;
            var file = Path.Combine(requests, id + ".json");
            File.WriteAllText(file, json.Serialize(request), new UTF8Encoding(false));
            var start=new ProcessStartInfo(Path.Combine(app,"python","python.exe"),"-X utf8 \""+Path.Combine(app,"core.py")+"\" --request \""+file+"\"") {WorkingDirectory=app,UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
            using(var p=new Process {StartInfo=start}) {
                p.Start();var errors=p.StandardError.ReadToEndAsync();
                string line;while((line=await p.StandardOutput.ReadLineAsync())!=null) {
                    try {
                        var row = json.Deserialize<Dictionary<string, object>>(line);
                        string message = Value(row, "message")
                            .Replace("“上游与更新”选择“重编当前版本”", "「安装管理 → 高级安装」选择「重编当前版本」")
                            .Replace("“按当前版本重编译”", "「安装管理 → 高级安装 → 重编当前版本」");
                        Append(message);
                        if (row.ContainsKey("error") && Convert.ToBoolean(row["error"])) failure = message;
                        if (!background && row.ContainsKey("progress")) {
                            var step = Dict(row["progress"]);
                            long total = Convert.ToInt64(step["total"]), done = Convert.ToInt64(step["done"]);
                            if (total > 0) {
                                progress.Style = ProgressBarStyle.Continuous;
                                progress.Maximum = 100;
                                progress.Value = (int)Math.Max(0, Math.Min(100, (double)done * 100 / total));
                                UpdateTaskStatus(progress.Value + "%  ·  " + message, Theme.Accent);
                            }
                        } else if (!background && message != "") {
                            UpdateTaskStatus(message, Theme.Accent);
                        }
                        if (row.ContainsKey("result")) result = Dict(row["result"]);
                    }
                    catch {Append(line);}
                }
                await Task.Run(()=>p.WaitForExit());var stderr=await errors;if(stderr!="")Append(stderr);success=p.ExitCode==0;
            }
        } catch(Exception e){failure=e.Message;Append("操作失败："+e.Message);}finally{if(!background){if(action!="inspect"&&action!="check"&&action!="discover"){lastFailed=!success;lastOutcome=success?(action.StartsWith("launch")?"游戏进程已启动":"操作完成，已校验"):"操作失败，请查看日志";}SetBusy(false);cancelFile="";}}
        if (!success && !background) {
            string detail = failure != "" ? failure : "详细原因见操作日志。";
            UpdateTaskStatus(detail, Theme.Warning);
            if (action != "inspect" && action != "check" && action != "discover" && action != "manager-check") {
                SetLogOpen(true);
                if (!selftest) MessageBox.Show(this, detail, action.StartsWith("launch") ? "启动未完成" : "操作未完成",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
        return success ? result??new Dictionary<string,object>() : null;
    }
    async void Choose(object sender,EventArgs e) {using(var d=new OpenFileDialog{Title="选择现有游戏的 totk.exe",Filter="游戏程序|totk.exe|应用程序|*.exe"})if(d.ShowDialog(this)==DialogResult.OK){selected=d.FileName;UpdatePath();Save();await Inspect();}}
    async void Find(object sender,EventArgs e) {
        using(var d=new FolderBrowserDialog{Description="选择游戏主目录，自动查找其中的安装"})if(d.ShowDialog()==DialogResult.OK) {
            var r=await Execute("discover",new Dictionary<string,object>{{"root",d.SelectedPath}});if(r==null)return;
            var games=r["games"] as System.Collections.ArrayList;var array=r["games"] as object[];
            var items=new List<Dictionary<string,object>>();if(games!=null)foreach(var g in games)items.Add(Dict(g));if(array!=null)foreach(var g in array)items.Add(Dict(g));
            if(items.Count==0){Append("未发现有效安装，请直接选择 totk.exe。");return;}
            if(items.Count==1){selected=Value(items[0],"game");}else {
                using(var picker=new Form {Text="选择要管理的游戏",Size=new Size(780,280),StartPosition=FormStartPosition.CenterParent}) {
                    var list=new ListBox {Dock=DockStyle.Fill};foreach(var g in items)list.Items.Add(Value(g,"game"));var ok=new Button {Text="使用所选安装",Dock=DockStyle.Bottom,Height=40,DialogResult=DialogResult.OK};picker.Controls.Add(list);picker.Controls.Add(ok);list.SelectedIndex=0;
                    if(picker.ShowDialog(this)!=DialogResult.OK)return;selected=Convert.ToString(list.SelectedItem);
                }
            }
            UpdatePath();Save();await Inspect();
        }
    }
    async Task Inspect() {
        compatibility = null;
        var r = await Execute("inspect");
        if (r == null) return;
        var ctx = Dict(r["context"]);
        object match;
        if (r.TryGetValue("compatibility", out match)) compatibility = Dict(match);
        gameVersion = Value(ctx, "version");
        lastHealth = Dict(r["health"]);
        var detail = Dict(r["components"]);
        current.Text = gameVersion;
        current.ForeColor = Value(compatibility, "status") == "newer" ? Theme.Warning : Theme.Text;
        string healthVersion = Value(lastHealth, "version");
        components.Text = (healthVersion != "" ? healthVersion + " · " : "") + Value(lastHealth, "status") +
            (Value(lastHealth, "recipe_outdated") == "True" ? "（有组件修复更新）" : "");
        components.ForeColor = Value(lastHealth, "status") == "正常" ? Theme.Accent : Theme.Lavender;
        string installedStyle = Value(detail, "icon_style");
        ps5State.Text = "PS5 图标 · " + Value(detail, "icons") +
            (installedStyle == "" ? "" : "（" + StyleName(installedStyle) + "）") + "\nUSB 触觉 · " + Value(detail, "ps5");
        graphicsState.Text = "图形组件 · " + Value(detail, "graphics") + "\nDLSS 库 " + Value(detail, "dlss") +
            "    AMD 帧生成库 " + Value(detail, "fsr");
        language.SelectedIndex = Math.Max(0, Array.IndexOf(languages, Value(r, "language")));
        Append("当前游戏：" + Value(ctx, "game"));
        FitCurrentPage();
    }
    async Task Check(bool manual) {
        var r=await Execute("check");if(r==null)return;latest=Value(r,"version");newest.Text="最新正式版 · "+latest;
        if(manual)Append(Value(r,"notes"));
        if(!manual&&selected!=""&&Value(lastHealth,"version")!=latest)Append("检查到上游版本信息。是否下载、升级和重编译由你主动选择，本次只提示。");
    }
    async Task Build(string action) {await Execute(action);if(selected!="")await Inspect();}
    async Task Action(string action,Dictionary<string,object> extra=null) {await Execute(action,extra);if(!action.StartsWith("launch")&&action!="official"&&selected!="")await Inspect();}
    async Task InstallIcons() {
        await Action("icons", new Dictionary<string, object> { { "style", iconStyles[Math.Max(0, iconStyle.SelectedIndex)] } });
    }
    async Task InstallComponent(bool controller) {
        await Inspect();
        string status=Value(compatibility,"status");
        if(controller&&ps5.Checked&&(status=="newer"||status=="unknown")) {
            string prompt=status=="newer"
                ? "游戏程序 "+gameVersion+" 比内置成品 "+Value(compatibility,"bundled")+" 新。完整 PS5 支持可使用对应免编译包，或在「安装管理 → 高级安装」选择「重编当前版本」。"
                : "当前游戏程序版本无法识别，暂时只能安装独立图标。";
            prompt+="\n\n现在只安装 PS5 图标，并保留当前游戏程序？";
            if(MessageBox.Show(this,prompt,"PS5 安装方式",MessageBoxButtons.YesNo,MessageBoxIcon.Information,MessageBoxDefaultButton.Button2)==DialogResult.Yes)
                await InstallIcons();
            return;
        }
        bool hasPs5=Value(lastHealth,"ps5")=="True",hasGraphics=Value(lastHealth,"graphics")=="True";
        var extra=new Dictionary<string,object>{{"ps5",controller?ps5.Checked:hasPs5},{"graphics",controller?hasGraphics:graphics.Checked}};
        await Execute("apply",extra);await Inspect();
    }
    async Task Repair() {await Execute("repair");await Inspect();}
    async void Export(object sender,EventArgs e) {using(var d=new SaveFileDialog{Filter="诊断包|*.zip",FileName="TotK诊断-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+".zip"})if(d.ShowDialog()==DialogResult.OK)await Execute("export",new Dictionary<string,object>{{"output",d.FileName}});}
    async Task DetectGpu() {
        string names=await Task.Run(()=>{try{var list=new List<string>();using(var query=new ManagementObjectSearcher("SELECT Name FROM Win32_VideoController"))foreach(var x in query.Get())list.Add(Convert.ToString(x["Name"]));return String.Join(" / ",list);}catch{return "未识别";}});
        gpu.Text=names;Append("显卡："+names);
        if(System.Text.RegularExpressions.Regex.IsMatch(names,@"RTX\s*[23]\d{3}",System.Text.RegularExpressions.RegexOptions.IgnoreCase))Append("此 RTX 型号支持 DLSS 超分，不支持 NVIDIA 原生 DLSS 帧生成；AMD FSR 帧生成能否启用还需运行时能力检查。");
    }
    [STAThread] public static void Main(string[] args) {
        selftest=args.Length>0&&args[0].StartsWith("--selftest");
        Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
        string folder = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
        string mutexName;
        using (var sha = SHA256.Create())
            mutexName = @"Local\TotKPatchManager-" + BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(folder.ToUpperInvariant())));
        bool ownsMutex;
        using (var instance = new Mutex(true, mutexName, out ownsMutex)) {
            if (!ownsMutex) return;
            try {
                var update = Path.Combine(folder, "cache", "manager-update");
                var trial = Path.Combine(update, "trial.json");
                string token = TrialToken(args, trial);
                if (token == "" && (File.Exists(trial) || File.Exists(Path.Combine(update, "ready.json")) ||
                    File.Exists(Path.Combine(update, "pending.json")))) {
                    try { StartUpdateWorker(folder); }
                    catch (Exception e) { MessageBox.Show(e.Message, "无法恢复管理器更新", MessageBoxButtons.OK, MessageBoxIcon.Error); }
                    return;
                }
                using (var window = new Manager(selftest ? PreviewOption(args, "--dpi") : 0)) {
                    if (selftest) window.Shown += async (s, e) => await window.RunPreview(args);
                    if (token != "") window.Shown += (s, e) => window.BeginInvoke(new Action(() =>
                        ConfirmManagerStartup(window, update, token)));
                    Application.Run(window);
                }
            } finally { instance.ReleaseMutex(); }
        }
    }
}
