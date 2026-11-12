using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

public partial class Manager {
    const string ModDrag = "TotK.Manager.ModFolder";
    readonly TextBox modSearch = new TextBox(), modDetail = new TextBox();
    readonly Label modStatus = new Label();
    ToggleSwitch modEnabled;
    ComboBox modFilter, modCategory, modSort, modProfiles;
    ListView modList;
    Dictionary<string,object> modSnapshot;
    string modGame = "";
    bool modFilling, modShowingDetails;

    IEnumerable<object> ModItems(Dictionary<string,object> data, string key) {
        object value;
        return data != null && data.TryGetValue(key,out value) && value is IEnumerable && !(value is string)
            ? ((IEnumerable)value).Cast<object>() : Enumerable.Empty<object>();
    }
    IEnumerable<Dictionary<string,object>> ModRows(string key) { return ModItems(modSnapshot,key).Select(Dict); }
    bool ModFlag(Dictionary<string,object> row,string key) { return Value(row,key) == "True"; }
    Dictionary<string,object> ModSelection() {
        return modGame==selected && modList.SelectedItems.Count==1 ? Dict(modList.SelectedItems[0].Tag) : null;
    }
    string[] ModIds() {
        if (modGame!=selected) return new string[0];
        var marked = modList.CheckedItems.Cast<ListViewItem>().ToArray();
        return (marked.Length != 0 ? marked : modList.SelectedItems.Cast<ListViewItem>().ToArray())
            .Select(i=>Dict(i.Tag)).Where(r=>!ModFlag(r,"protected")).Select(r=>Value(r,"id")).ToArray();
    }
    void ModText(TextBox control) {
        control.BackColor=Theme.Canvas;control.ForeColor=Theme.Text;control.Font=regular;
        control.BorderStyle=BorderStyle.FixedSingle;
    }
    void BuildModCenter() {
        var page = Page("MOD 管理","与游戏 F1 共用 MOD 文件夹、启停和加载顺序。");
        modFilter = new DarkCombo(density);
        modCategory = new DarkCombo(density);
        modSort = new DarkCombo(density);
        modProfiles = new DarkCombo(density);
        MakeCombo(modFilter,new object[]{"全部状态","已启用","已禁用","有冲突","已收藏","受保护"},false);
        MakeCombo(modCategory,new object[]{"全部分类"},false);
        MakeCombo(modSort,new object[]{"加载优先级","名称","安装时间","收藏优先"},false);
        MakeCombo(modProfiles,new object[]{"纯净游戏","PS5 手柄","画质增强"},false);
        modSort.Width=D(151);
        foreach (var filter in new[] {modFilter,modCategory,modSort}) filter.SelectedIndexChanged += (s,e)=>PopulateMods();
        ModText(modSearch);
        modSearch.TextChanged += (s,e)=>PopulateMods();
        modList = new ListView { Dock=DockStyle.Fill, View=View.Details, FullRowSelect=true, CheckBoxes=true,
            HideSelection=false, MultiSelect=true, BorderStyle=BorderStyle.None, BackColor=Theme.Canvas,
            ForeColor=Theme.Text, Font=regular, AllowDrop=true, OwnerDraw=true, HeaderStyle=ColumnHeaderStyle.Nonclickable };
        modList.Columns.Add("MOD",D(185));modList.Columns.Add("状态",D(84));modList.Columns.Add("顺序",D(55));
        modList.DrawColumnHeader += (s,e)=>{
            using (var surface=new SolidBrush(Theme.Input)) e.Graphics.FillRectangle(surface,e.Bounds);
            var bounds=new Rectangle(e.Bounds.X+D(7),e.Bounds.Y,e.Bounds.Width-D(7),e.Bounds.Height);
            TextRenderer.DrawText(e.Graphics,e.Header.Text,small,bounds,Theme.Muted,TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
        };
        modList.DrawItem += (s,e)=>e.DrawDefault=true;
        modList.DrawSubItem += (s,e)=>e.DrawDefault=true;
        modList.SelectedIndexChanged += (s,e)=>ShowModDetails();
        modList.ItemCheck += (s,e)=>{
            if (ModFlag(Dict(modList.Items[e.Index].Tag),"protected")) e.NewValue=CheckState.Unchecked;
        };
        modList.ItemDrag += (s,e)=>{
            var row=Dict(((ListViewItem)e.Item).Tag);
            if (modFilter.SelectedIndex!=0 || modCategory.SelectedIndex!=0 || modSort.SelectedIndex!=0 ||
                modSearch.Text!="" || ModFlag(row,"protected")) return;
            var data=new DataObject();data.SetData(ModDrag,Value(row,"id"));modList.DoDragDrop(data,DragDropEffects.Move);
        };
        modList.DragEnter += (s,e)=>e.Effect=e.Data.GetDataPresent(DataFormats.FileDrop)?DragDropEffects.Copy:
            e.Data.GetDataPresent(ModDrag)?DragDropEffects.Move:DragDropEffects.None;
        modList.DragDrop += async (s,e)=>{
            if (e.Data.GetDataPresent(DataFormats.FileDrop)) {await PreviewMods((string[])e.Data.GetData(DataFormats.FileDrop));return;}
            if (!e.Data.GetDataPresent(ModDrag)) return;
            var pt=modList.PointToClient(new Point(e.X,e.Y));
            var item=modList.GetItemAt(pt.X,pt.Y);
            if (item==null) return;
            var order=ModRows("mods").Select(r=>Value(r,"id")).ToList();
            string id=Convert.ToString(e.Data.GetData(ModDrag)), target=Value(Dict(item.Tag),"id");
            if (id==target || !order.Remove(id) || !order.Contains(target)) return;
            int index=order.IndexOf(target);
            if (pt.Y>=item.Bounds.Top+item.Bounds.Height/2) index++;
            order.Insert(index,id);
            await ChangeMods("mods-order",new Dictionary<string,object>{{"order",order}});
        };
        ModText(modDetail);
        modDetail.Dock=DockStyle.Fill;modDetail.ReadOnly=true;modDetail.Multiline=true;modDetail.ScrollBars=ScrollBars.Vertical;
        modEnabled=new ToggleSwitch(density) {Text="启用所选 MOD",Dock=DockStyle.Top,Font=regular,Height=D(36),Enabled=false};
        modEnabled.CheckedChanged += async (s,e)=>{
            var row=ModSelection();
            if (modShowingDetails || row==null || !modEnabled.Enabled) return;
            if (busy) {ShowModDetails();return;}
            if (modEnabled.Checked && ModConflict(Value(row,"id")) && !ModConfirm("该 MOD 有诊断项。仍要启用吗？")) {ShowModDetails();return;}
            await ChangeMods("mods-toggle",new Dictionary<string,object>{{"ids",new[]{Value(row,"id")}},{"enabled",modEnabled.Checked}});
            ShowModDetails();
        };
        var pair=new SplitContainer {Height=D(270),BackColor=Theme.Border,SplitterWidth=D(5)};
        pair.Panel2.BackColor=Theme.Canvas;
        pair.Panel1.Controls.Add(modList);pair.Panel2.Controls.Add(modDetail);pair.Panel2.Controls.Add(modEnabled);
        pair.Resize += (s,e)=>{
            if (pair.Width >= D(200)) pair.SplitterDistance=pair.Width*49/100;
            modList.Columns[0].Width=Math.Max(D(80),pair.Panel1.Width-D(145));
        };
        Card(page,"MOD 工作区","关闭游戏后编辑。资源和优先级变化在下次启动时生效。",
            Status(modStatus,"请选择游戏并刷新。",Theme.Text),
            Row(Pick(),Command("mods-scan","刷新",Symbol.Refresh,ButtonTone.Secondary,async()=>await RefreshMods()),
                Command("mods-import","导入 ZIP / 更新",Symbol.Download,ButtonTone.Primary,async()=>await PickMod(false)),
                Command("mods-folder","导入文件夹",Symbol.Folder,ButtonTone.Secondary,async()=>await PickMod(true)),
                Command("mods-undo","撤销 / 恢复",Symbol.Refresh,ButtonTone.Quiet,async()=>{
                    if (ModConfirm("撤销最近一次操作，或恢复中断的事务？"))
                        await ChangeMods(ModFlag(modSnapshot,"pending")?"mods-recover":"mods-undo",null);
                })),
            Status(new Label(),"搜索名称、作者、版本或备注",Theme.Text),modSearch,
            Row(modFilter,modCategory,modSort),pair,
            Hint("勾选进行批量操作。按加载优先级显示且无筛选时可拖动排序。ZIP 和文件夹可拖到列表。"),
            Row(Command("mods-enable","启用",Symbol.Check,ButtonTone.Secondary,async()=>await ToggleMods(true,false)),
                Command("mods-disable","禁用",Symbol.Close,ButtonTone.Secondary,async()=>await ToggleMods(false,false)),
                Command("mods-remove","卸载",Symbol.Package,ButtonTone.Secondary,async()=>await RemoveMods()),
                Track("mods-locate","定位文件",Symbol.Folder,ButtonTone.Quiet,(s,e)=>OpenModFolder()),
                Command("mods-notes","备注 / 分类",Symbol.Log,ButtonTone.Quiet,async()=>await EditModNote())),
            Row(Track("mods-select-all","全选",Symbol.Check,ButtonTone.Quiet,(s,e)=>SelectMods("all")),
                Track("mods-select-invert","反选",Symbol.Refresh,ButtonTone.Quiet,(s,e)=>SelectMods("invert")),
                Track("mods-select-active","选中已启用",Symbol.Check,ButtonTone.Quiet,(s,e)=>SelectMods("on")),
                Track("mods-select-conflicts","选中冲突",Symbol.Search,ButtonTone.Quiet,(s,e)=>SelectMods("conflicts")),
                Command("mods-all-on","启用全部普通 MOD",Symbol.Check,ButtonTone.Quiet,async()=>await ToggleMods(true,true)),
                Command("mods-all-off","禁用全部普通 MOD",Symbol.Close,ButtonTone.Quiet,async()=>await ToggleMods(false,true))));
        Card(page,"配置方案","方案保存 MOD 选择和顺序。内置组件按原状态保留。",
            modProfiles,Row(Command("mods-profile","预览并切换",Symbol.Refresh,ButtonTone.Primary,async()=>await SwitchModProfile()),
                Command("mods-profile-edit","管理方案",Symbol.Package,ButtonTone.Secondary,async()=>await EditModProfile())));
    }

    async Task RefreshMods() {
        if (selected=="" || busy) return;
        if (modGame!=selected) {modSnapshot=null;modList.Items.Clear();modStatus.Text="正在读取游戏 MOD…";}
        modSnapshot=await Execute("mods-scan");
        if (modSnapshot==null) {modList.Items.Clear();modStatus.Text="读取失败，请查看日志后刷新。";ShowModDetails();return;}
        modGame=selected;
        modStatus.Text="上游 "+Value(modSnapshot,"runtime")+" · 游戏资源 "+
            (Value(modSnapshot,"game_version")==""?"未识别":Value(modSnapshot,"game_version"))+
            " · "+(ModFlag(modSnapshot,"writable")?"支持启停与优先级":"当前仅可查看")+
            (ModFlag(modSnapshot,"pending")?" · 有中断的事务":"");
        string category=Convert.ToString(modCategory.SelectedItem), profile=Convert.ToString(modProfiles.SelectedItem);
        modFilling=true;
        try {
            modCategory.Items.Clear();modCategory.Items.Add("全部分类");
            modCategory.Items.AddRange(ModRows("mods").Select(r=>Value(r,"category")).Distinct().OrderBy(x=>x).Cast<object>().ToArray());
            modCategory.SelectedIndex=Math.Max(0,modCategory.Items.IndexOf(category));
            modProfiles.Items.Clear();modProfiles.Items.AddRange(ModItems(modSnapshot,"profiles").ToArray());
            modProfiles.SelectedIndex=Math.Max(0,modProfiles.Items.IndexOf(profile));
        } finally {modFilling=false;}
        PopulateMods();FitCurrentPage();
    }
    IEnumerable<Dictionary<string,object>> ModProblems(string id) {
        return ModRows("conflicts").Where(x=>ModItems(x,"mods").Select(Convert.ToString).Contains(id));
    }
    bool ModConflict(string id) {
        return ModProblems(id).Any(r=>!new[]{"相同","可共存"}.Contains(Value(r,"type")));
    }
    string FormatProblems(IEnumerable<Dictionary<string,object>> rows) {
        return String.Join(Environment.NewLine,rows.Take(200).Select(r=>"["+Value(r,"type")+"] "+
            String.Join(" / ",ModItems(r,"mods").Select(Convert.ToString))+" · "+Value(r,"file")+Environment.NewLine+Value(r,"reason")));
    }
    void PopulateMods() {
        if (modFilling || modList==null || modEnabled==null) return;
        var marked=new HashSet<string>(modList.CheckedItems.Cast<ListViewItem>().Select(i=>Value(Dict(i.Tag),"id")));
        string active=Value(ModSelection(),"id"), search=modSearch.Text.Trim();
        var rows=ModRows("mods").Where(r=>search=="" || new[]{"name","version","author","note","category"}
            .Any(k=>Value(r,k).IndexOf(search,StringComparison.OrdinalIgnoreCase)>=0));
        if (modCategory.SelectedIndex>0) rows=rows.Where(r=>Value(r,"category")==Convert.ToString(modCategory.SelectedItem));
        switch (modFilter.SelectedIndex) {
            case 1: rows=rows.Where(r=>ModFlag(r,"enabled"));break;
            case 2: rows=rows.Where(r=>!ModFlag(r,"enabled"));break;
            case 3: rows=rows.Where(r=>ModConflict(Value(r,"id")));break;
            case 4: rows=rows.Where(r=>ModFlag(r,"favorite"));break;
            case 5: rows=rows.Where(r=>ModFlag(r,"protected"));break;
        }
        if (modSort.SelectedIndex==1) rows=rows.OrderBy(r=>Value(r,"name"));
        if (modSort.SelectedIndex==2) rows=rows.OrderByDescending(r=>Value(r,"installed"));
        if (modSort.SelectedIndex==3) rows=rows.OrderByDescending(r=>ModFlag(r,"favorite"));
        modList.BeginUpdate();
        try {
            modList.Items.Clear();
            foreach (var r in rows) {
                string id=Value(r,"id");
                var item=new ListViewItem(new[]{(ModFlag(r,"favorite")?"★ ":"")+Value(r,"name"),
                    ModFlag(r,"protected")?"受保护":Value(r,"problem")!=""?"需检查":ModFlag(r,"enabled")?"已启用":"已禁用",
                    Value(r,"priority")}){Tag=r};
                if (ModConflict(id)) item.ForeColor=Theme.Warning;
                modList.Items.Add(item);item.Checked=marked.Contains(id);item.Selected=id==active;
            }
        } finally {modList.EndUpdate();}
        ShowModDetails();
    }
    void ShowModDetails() {
        var row=ModSelection();
        modShowingDetails=true;
        modEnabled.Checked=ModFlag(row,"enabled");
        modEnabled.Enabled=row!=null && ModFlag(modSnapshot,"writable") && !ModFlag(row,"protected") &&
            (Value(row,"problem")=="" || modEnabled.Checked);
        modShowingDetails=false;
        if (row==null) {modDetail.Text="选择一项查看详情。\r\n\r\nMOD 目录\r\n"+Value(modSnapshot,"folder");return;}
        modDetail.Text=String.Join("\r\n",new[]{Value(row,"name")+"  "+Value(row,"version"),"作者 · "+Value(row,"author"),
            "类型 · "+Value(row,"kind")+" · "+Value(row,"file_count")+" 个文件","来源 · "+Value(row,"source"),
            "安装时间 · "+Value(row,"installed"),Value(row,"path"),"",Value(row,"description"),
            Value(row,"note"),Value(row,"loaded"),"",FormatProblems(ModProblems(Value(row,"id")))});
    }
    bool ModConfirm(string text) {
        return MessageBox.Show(this,text,"MOD 管理",MessageBoxButtons.YesNo,MessageBoxIcon.Question,
            MessageBoxDefaultButton.Button2)==DialogResult.Yes;
    }
    async Task ChangeMods(string action,Dictionary<string,object> args) {
        if (await Execute(action,args)!=null) await RefreshMods();
    }
    void SelectMods(string mode) {
        var items=modList.Items.Cast<ListViewItem>().Where(i=>!ModFlag(Dict(i.Tag),"protected")).ToArray();
        foreach (var item in items) {
            var row=Dict(item.Tag);
            item.Checked=mode=="all" || (mode=="on"?ModFlag(row,"enabled"):mode=="conflicts"?ModConflict(Value(row,"id")):!item.Checked);
        }
    }
    async Task ToggleMods(bool enabled,bool all) {
        var ids=all?ModRows("mods").Where(r=>!ModFlag(r,"protected")&&(!enabled||Value(r,"problem")==""))
            .Select(r=>Value(r,"id")).ToArray():ModIds();
        if (ids.Length==0) return;
        if ((enabled||all)&&!ModConfirm((enabled?"启用 ":"禁用 ")+ids.Length+" 个普通 MOD？资源按列表优先级覆盖，重启后生效。")) return;
        await ChangeMods("mods-toggle",new Dictionary<string,object>{{"ids",ids},{"enabled",enabled}});
    }
    async Task RemoveMods() {
        var ids=ModIds();
        if (ids.Length!=0 && ModConfirm("卸载以下 MOD？所选文件夹会移入可恢复备份。\n\n"+String.Join("\n",ids)))
            await ChangeMods("mods-remove",new Dictionary<string,object>{{"ids",ids}});
    }
    void OpenModFolder() {
        string folder=Value(ModSelection(),"path");
        if (Directory.Exists(folder)) Process.Start(new ProcessStartInfo(folder){UseShellExecute=true});
    }
    async Task PickMod(bool folder) {
        if (folder) {
            using (var picker=new FolderBrowserDialog { Description="选择 MOD 或多个 MOD 的上一级文件夹" })
                if (picker.ShowDialog(this)==DialogResult.OK) await PreviewMods(new[]{picker.SelectedPath});
        } else {
            using (var picker=new OpenFileDialog { Title="选择 TotK MOD",Filter="ZIP|*.zip|所有文件|*.*",Multiselect=true })
                if (picker.ShowDialog(this)==DialogResult.OK) await PreviewMods(picker.FileNames);
        }
    }
    async Task PreviewMods(string[] files) {
        var args=new Dictionary<string,object>{{"sources",files},{"replace",true}};
        var preview=await Execute("mods-preview",args);
        if (preview==null) return;
        var report=new StringBuilder();
        foreach (var row in ModItems(preview,"entries").Select(Dict)) {
            report.AppendLine(Value(row,"decision")+" · "+Value(row,"id")).AppendLine(Value(row,"destination"));
            foreach (var file in ModItems(row,"files")) report.Append("  ").AppendLine(Convert.ToString(file));
            report.AppendLine();
        }
        report.Append(FormatProblems(ModItems(preview,"conflicts").Select(Dict)));
        bool replace;
        if (!ModReview("导入前预览",report.ToString(),true,out replace)) return;
        args["replace"]=replace;args["token"]=Value(preview,"token");
        await ChangeMods("mods-install",args);
    }
    async Task SwitchModProfile() {
        var args=new Dictionary<string,object>{{"profile",Convert.ToString(modProfiles.SelectedItem)}};
        var result=await Execute("mods-profile-preview",args);
        if (result==null) return;
        string content="将启用\r\n"+String.Join("\r\n",ModItems(result,"enabled").Select(Convert.ToString))+
            "\r\n\r\n缺失项目\r\n"+String.Join("\r\n",ModItems(result,"missing").Select(Convert.ToString))+
            "\r\n\r\n"+FormatProblems(ModItems(result,"conflicts").Select(Dict));
        bool unused;
        if (!ModReview("切换配置方案",content,false,out unused)) return;
        args["token"]=Value(result,"token");await ChangeMods("mods-profile",args);
    }
    Form ModDialog(string title,int width,int height) {
        return new Form {Text=title,ClientSize=new Size(D(width),D(height)),StartPosition=FormStartPosition.CenterParent,
            BackColor=Theme.Card,ForeColor=Theme.Text,Font=regular,MinimizeBox=false,MaximizeBox=false,ShowInTaskbar=false};
    }
    bool ModReview(string title,string content,bool replacement,out bool replace) {
        using (var dialog=ModDialog(title,670,500)) {
            var text=new TextBox {Dock=DockStyle.Fill,Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Both,Text=content,WordWrap=false};
            ModText(text);
            var bottom=new FlowLayoutPanel {Dock=DockStyle.Bottom,Height=D(53),FlowDirection=FlowDirection.RightToLeft,Padding=Insets(6)};
            var choice=new CheckBox {Text="替换内容不同的同名 MOD",AutoSize=true};
            bottom.Controls.Add(MakeButton("取消",Symbol.Close,ButtonTone.Secondary,(s,e)=>dialog.DialogResult=DialogResult.Cancel));
            bottom.Controls.Add(MakeButton("确认",Symbol.Check,ButtonTone.Primary,(s,e)=>dialog.DialogResult=DialogResult.OK));
            if (replacement) bottom.Controls.Add(choice);
            dialog.Controls.Add(text);dialog.Controls.Add(bottom);
            bool accepted=dialog.ShowDialog(this)==DialogResult.OK;
            replace=choice.Checked;return accepted;
        }
    }
    string ModInput(string title,string initial) {
        using (var dialog=ModDialog(title,460,125)) {
            var value=new TextBox {Text=initial,Left=D(12),Top=D(14),Width=D(430)};
            ModText(value);
            var ok=MakeButton("保存",Symbol.Check,ButtonTone.Primary,(s,e)=>dialog.DialogResult=DialogResult.OK);
            ok.Location=new Point(D(12),D(54));dialog.Controls.Add(value);dialog.Controls.Add(ok);dialog.AcceptButton=ok;
            return dialog.ShowDialog(this)==DialogResult.OK?value.Text.Trim():null;
        }
    }
    async Task EditModNote() {
        var row=ModSelection();
        if (row==null || ModFlag(row,"protected")) return;
        using (var dialog=ModDialog("MOD 备注与分类",465,205)) {
            var category=new TextBox {Text=Value(row,"category"),Width=D(425),Left=D(15),Top=D(12)};
            var note=new TextBox {Text=Value(row,"note"),Multiline=true,Width=D(425),Height=D(74),Left=D(15),Top=D(45)};
            ModText(category);ModText(note);
            var favorite=new CheckBox {Text="收藏",Checked=ModFlag(row,"favorite"),Left=D(15),Top=D(124),AutoSize=true};
            var ok=MakeButton("保存",Symbol.Check,ButtonTone.Primary,(s,e)=>dialog.DialogResult=DialogResult.OK);
            ok.Location=new Point(D(15),D(156));dialog.Controls.AddRange(new Control[]{category,note,favorite,ok});
            if (dialog.ShowDialog(this)==DialogResult.OK)
                await ChangeMods("mods-notes",new Dictionary<string,object>{{"ids",new[]{Value(row,"id")}},
                    {"category",category.Text},{"note",note.Text},{"favorite",favorite.Checked}});
        }
    }
    async Task EditModProfile() {
        string source=Convert.ToString(modProfiles.SelectedItem),mode;
        using (var dialog=ModDialog("管理配置方案",405,115)) {
            var list=new DarkCombo(density){Left=D(12),Top=D(12),Width=D(370)};
            list.Items.AddRange(new object[]{"保存当前状态","复制所选","重命名所选","删除所选","导入 JSON","导出 JSON"});
            list.SelectedIndex=0;
            var ok=MakeButton("继续",Symbol.Check,ButtonTone.Primary,(s,e)=>dialog.DialogResult=DialogResult.OK);
            ok.Location=new Point(D(12),D(53));dialog.Controls.Add(list);dialog.Controls.Add(ok);
            if (dialog.ShowDialog(this)!=DialogResult.OK) return;
            mode=new[]{"create","copy","rename","delete","import","export"}[list.SelectedIndex];
        }
        var args=new Dictionary<string,object>{{"source",source},{"profile",source},{"mode",mode}};
        if (mode=="export") {
            var result=await Execute("mods-profile-export",args);
            if (result==null) return;
            using (var file=new SaveFileDialog {Filter="MOD 方案 JSON|*.json",FileName="TotK-MOD-Profile.json"})
                if (file.ShowDialog(this)==DialogResult.OK) File.WriteAllText(file.FileName,json.Serialize(result),new UTF8Encoding(false));
            return;
        }
        if (mode=="delete") {
            if (!ModConfirm("删除配置方案「"+source+"」？")) return;
        } else {
            string name=ModInput("方案名称",mode=="rename"?source:"");
            if (String.IsNullOrEmpty(name)) return;
            args["profile"]=name;
            if (mode=="create" && modProfiles.Items.Contains(name) && !ModConfirm("覆盖同名方案？")) return;
        }
        if (mode=="import") {
            using (var file=new OpenFileDialog {Filter="MOD 方案 JSON|*.json"}) {
                if (file.ShowDialog(this)!=DialogResult.OK) return;
                try {
                    if (new FileInfo(file.FileName).Length>1024*1024) throw new IOException("方案文件过大。");
                    args["data"]=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file.FileName));
                } catch (Exception error) {MessageBox.Show(this,error.Message,"方案导入失败");return;}
            }
        }
        await ChangeMods("mods-profile-edit",args);
    }
}
