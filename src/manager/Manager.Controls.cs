using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

static class Theme {
    public static readonly Color Canvas = Color.FromArgb(16, 21, 33);
    public static readonly Color Rail = Color.FromArgb(20, 26, 40);
    public static readonly Color Card = Color.FromArgb(25, 33, 50);
    public static readonly Color Hero = Color.FromArgb(28, 43, 54);
    public static readonly Color Input = Color.FromArgb(16, 23, 36);
    public static readonly Color Button = Color.FromArgb(36, 47, 67);
    public static readonly Color Hover = Color.FromArgb(47, 61, 83);
    public static readonly Color Border = Color.FromArgb(51, 65, 88);
    public static readonly Color Text = Color.FromArgb(237, 242, 251);
    public static readonly Color Muted = Color.FromArgb(169, 184, 207);
    public static readonly Color Faint = Color.FromArgb(130, 148, 174);
    public static readonly Color Accent = Color.FromArgb(103, 229, 192);
    public static readonly Color Lavender = Color.FromArgb(178, 168, 249);
    public static readonly Color Warning = Color.FromArgb(255, 174, 175);

    public static void Rounded(Graphics g, RectangleF rect, float radius, Color fill, Color border) {
        if (rect.Width < 2 || rect.Height < 2) return;
        float diameter = Math.Min(radius * 2, Math.Min(rect.Width, rect.Height));
        using (var path = new GraphicsPath()) {
            path.AddArc(rect.Left, rect.Top, diameter, diameter, 180, 90);
            path.AddArc(rect.Right - diameter, rect.Top, diameter, diameter, 270, 90);
            path.AddArc(rect.Right - diameter, rect.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(rect.Left, rect.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            using (var brush = new SolidBrush(fill)) g.FillPath(brush, path);
            if (border != Color.Empty)
                using (var pen = new Pen(border)) g.DrawPath(pen, path);
        }
    }
}

enum Symbol { None, Brand, Home, Controller, Display, Package, Shield, Settings, Play, Folder,
              Search, Refresh, Download, Log, Chevron, Check, Close, Copy }
enum ButtonTone { Primary, Secondary, Quiet, Navigation }

static class Symbols {
    static PointF P(float x, float y) { return new PointF(x, y); }
    public static void Paint(Graphics g, Symbol icon, Rectangle rect, Color color) {
        if (icon == Symbol.None || rect.Width <= 0 || rect.Height <= 0) return;
        var state = g.Save();
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TranslateTransform(rect.Left, rect.Top);
        g.ScaleTransform(rect.Width / 24f, rect.Height / 24f);
        using (var pen = new Pen(color, 1.65f) { StartCap = LineCap.Round, EndCap = LineCap.Round, LineJoin = LineJoin.Round })
        using (var brush = new SolidBrush(color)) {
            switch (icon) {
                case Symbol.Brand:
                    g.FillPolygon(brush, new[] { P(12,2),P(17,10.6f),P(7,10.6f) });
                    g.FillPolygon(brush, new[] { P(6.3f,11.9f),P(11.4f,20.6f),P(1.2f,20.6f) });
                    g.FillPolygon(brush, new[] { P(17.7f,11.9f),P(22.8f,20.6f),P(12.6f,20.6f) });
                    break;
                case Symbol.Home:
                    g.DrawLines(pen, new[] { P(3,11),P(12,4),P(21,11) });
                    g.DrawLines(pen, new[] { P(5,10),P(5,20),P(10,20),P(10,15),P(14,15),P(14,20),P(19,20),P(19,10) });
                    break;
                case Symbol.Controller:
                    g.DrawPolygon(pen, new[] { P(7,7),P(17,7),P(20,10),P(22,17),P(19,19),P(15,15),P(9,15),P(5,19),P(2,17),P(4,10) });
                    g.DrawLine(pen, 6,11,10,11); g.DrawLine(pen, 8,9,8,13);
                    g.FillEllipse(brush, 15,9,2,2); g.FillEllipse(brush, 18,11,2,2);
                    break;
                case Symbol.Display:
                    g.DrawRectangle(pen, 3,5,18,12); g.DrawLine(pen, 12,17,12,21); g.DrawLine(pen, 8,21,16,21);
                    break;
                case Symbol.Package:
                    g.DrawPolygon(pen, new[] { P(12,3),P(21,8),P(21,17),P(12,22),P(3,17),P(3,8) });
                    g.DrawLines(pen, new[] { P(3,8),P(12,13),P(21,8) }); g.DrawLine(pen,12,13,12,22);
                    break;
                case Symbol.Shield:
                    g.DrawPolygon(pen, new[] { P(12,3),P(20,6),P(19,15),P(12,21),P(5,15),P(4,6) });
                    g.DrawLines(pen, new[] { P(8,12),P(11,15),P(16,9) });
                    break;
                case Symbol.Settings:
                    for (int i = 0; i < 3; i++) {
                        int y = 6 + i * 6, x = i == 1 ? 15 : 8;
                        g.DrawLine(pen, 3,y,x-2,y); g.DrawLine(pen,x+2,y,21,y);
                        g.DrawEllipse(pen,x-2,y-2,4,4);
                    }
                    break;
                case Symbol.Play: g.DrawPolygon(pen, new[] { P(7,4),P(20,12),P(7,20) }); break;
                case Symbol.Folder:
                    g.DrawPolygon(pen, new[] { P(3,6),P(9,6),P(12,9),P(21,9),P(21,19),P(3,19) }); break;
                case Symbol.Search: g.DrawEllipse(pen,4,4,12,12); g.DrawLine(pen,14,14,21,21); break;
                case Symbol.Refresh:
                    g.DrawArc(pen,4,4,16,16,35,288);
                    g.DrawLines(pen,new[] { P(20,5),P(20,10),P(15,10) }); break;
                case Symbol.Download:
                    g.DrawLine(pen,12,3,12,15); g.DrawLines(pen,new[] { P(7,10),P(12,15),P(17,10) });
                    g.DrawLines(pen,new[] { P(4,16),P(4,20),P(20,20),P(20,16) }); break;
                case Symbol.Log:
                    g.DrawRectangle(pen,5,3,14,18);
                    for (int y = 8; y <= 16; y += 4) g.DrawLine(pen,8,y,16,y);
                    break;
                case Symbol.Chevron: g.DrawLines(pen,new[] { P(6,9),P(12,15),P(18,9) }); break;
                case Symbol.Check: g.DrawLines(pen,new[] { P(4,12),P(10,18),P(20,6) }); break;
                case Symbol.Close: g.DrawLine(pen,6,6,18,18); g.DrawLine(pen,18,6,6,18); break;
                case Symbol.Copy: g.DrawRectangle(pen,8,8,12,12); g.DrawLines(pen,new[] { P(4,16),P(4,4),P(16,4) }); break;
            }
        }
        g.Restore(state);
    }
}

sealed class ModernButton : Button {
    readonly float scale;
    bool hover, pressed;
    public Symbol Icon { get; set; }
    public ButtonTone Tone { get; set; }
    public bool Selected { get; set; }
    public bool Compact { get; set; }
    public bool Expanded { get; set; }

    public ModernButton(float scale) {
        this.scale = scale;
        FlatStyle = FlatStyle.Flat;
        FlatAppearance.BorderSize = 0;
        UseVisualStyleBackColor = false;
        Cursor = Cursors.Hand;
        SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
    }
    int D(int n) { return Math.Max(1, (int)Math.Round(n * scale)); }
    protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { hover = false; pressed = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { pressed = true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { pressed = false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnGotFocus(EventArgs e) { base.OnGotFocus(e); Invalidate(); }
    protected override void OnLostFocus(EventArgs e) { base.OnLostFocus(e); Invalidate(); }
    protected override void OnPaint(PaintEventArgs e) {
        var g = e.Graphics;
        g.Clear(Parent == null ? Theme.Canvas : Parent.BackColor);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        bool nav = Tone == ButtonTone.Navigation, primary = Tone == ButtonTone.Primary;
        Color surface = Theme.Button, text = Theme.Text, outline = Theme.Border;
        if (primary) {
            surface = hover ? Color.FromArgb(139, 242, 212) : Theme.Accent;
            text = Theme.Canvas;
            outline = Color.Empty;
        } else if (nav || Tone == ButtonTone.Quiet) {
            surface = Selected ? Color.FromArgb(39, 48, 73) : Parent == null ? Theme.Rail : Parent.BackColor;
            outline = Color.Empty;
            text = Selected ? Theme.Text : Theme.Muted;
        }
        if ((hover || pressed) && !primary) surface = Theme.Hover;
        if (pressed && primary) surface = Color.FromArgb(77, 198, 164);
        if (!Enabled) { surface = Theme.Card; text = Theme.Faint; }
        if (Focused && ShowFocusCues) outline = primary ? Theme.Text : Theme.Accent;
        Theme.Rounded(g, new RectangleF(.5f, .5f, Width - 1, Height - 1), D(8), surface, outline);
        if (nav && Selected) {
            Theme.Rounded(g, new RectangleF(D(3), D(9), D(3), Height - D(18)), D(2), Theme.Accent, Color.Empty);
        }
        int size = D(nav ? 19 : 16);
        int iconX = Compact ? (Width - size) / 2 : D(nav ? 14 : 11);
        if (Icon != Symbol.None) {
            var icon = new Rectangle(iconX, (Height - size) / 2, size, size);
            if (Icon == Symbol.Chevron && Expanded) {
                var state = g.Save();
                g.TranslateTransform(icon.Left * 2 + size, icon.Top * 2 + size);
                g.RotateTransform(180);
                Symbols.Paint(g, Icon, icon, text);
                g.Restore(state);
            } else Symbols.Paint(g, Icon, icon, nav && Selected ? Theme.Accent : text);
        }
        if (!Compact) {
            int left = Icon == Symbol.None ? D(10) : iconX + size + D(8);
            var rect = new Rectangle(left, 0, Math.Max(0, Width - left - D(8)), Height);
            var align = Icon == Symbol.None ? TextFormatFlags.HorizontalCenter : TextFormatFlags.Left;
            TextRenderer.DrawText(g, Text, Font, rect, text, align | TextFormatFlags.VerticalCenter |
                TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix | TextFormatFlags.EndEllipsis);
        }
    }
}

sealed class ToggleSwitch : CheckBox {
    readonly int track, gap;
    public ToggleSwitch(float scale) {
        track = (int)Math.Round(35 * scale);
        gap = (int)Math.Round(10 * scale);
        AutoSize = false;
        SetStyle(ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        Cursor = Cursors.Hand;
        CheckedChanged += (s, e) => Invalidate();
        GotFocus += (s, e) => Invalidate();
        LostFocus += (s, e) => Invalidate();
    }
    public void Fit(int width) {
        Width = width;
        var text = TextRenderer.MeasureText(Text, Font, new Size(Math.Max(1, width - track - gap - 6), int.MaxValue),
            TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix);
        Height = Math.Max(track, text.Height + gap);
    }
    protected override void OnPaint(PaintEventArgs e) {
        var g = e.Graphics;
        g.Clear(Parent.BackColor);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        int h = track * 3 / 5, y = (Height - h) / 2, dot = h - 6;
        var color = Checked && Enabled ? Theme.Accent : Theme.Hover;
        Theme.Rounded(g, new RectangleF(1, y, track, h), h / 2f, color,
            Focused && ShowFocusCues ? Theme.Lavender : Theme.Border);
        using (var brush = new SolidBrush(Checked && Enabled ? Theme.Canvas : Theme.Muted))
            g.FillEllipse(brush, Checked ? track - dot - 2 : 4, y + 3, dot, dot);
        TextRenderer.DrawText(g, Text, Font, new Rectangle(track + gap, 0, Width - track - gap, Height),
            Enabled ? Theme.Text : Theme.Faint, TextFormatFlags.VerticalCenter | TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix);
    }
}

sealed class DarkCombo : ComboBox {
    readonly int inset, icon;
    public DarkCombo(float scale) {
        inset = (int)Math.Round(9 * scale);
        icon = (int)Math.Round(17 * scale);
        SetStyle(ControlStyles.UserPaint | ControlStyles.ResizeRedraw | ControlStyles.OptimizedDoubleBuffer, true);
        SelectedIndexChanged += (s, e) => Invalidate();
        GotFocus += (s, e) => Invalidate();
        LostFocus += (s, e) => Invalidate();
        EnabledChanged += (s, e) => Invalidate();
    }
    protected override void OnPaint(PaintEventArgs e) {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Parent == null ? Theme.Card : Parent.BackColor);
        Theme.Rounded(g, new RectangleF(.5f, .5f, Width - 1, Height - 1), inset - 2, Theme.Input,
            Focused ? Theme.Accent : Theme.Border);
        var text = new Rectangle(inset, 0, Math.Max(1, Width - 3 * inset - icon), Height);
        TextRenderer.DrawText(g, Text, Font, text, Enabled ? Theme.Text : Theme.Faint,
            TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix | TextFormatFlags.EndEllipsis);
        Symbols.Paint(g, Symbol.Chevron, new Rectangle(Width - inset - icon, (Height - icon) / 2, icon, icon), Theme.Muted);
    }
}

sealed class PageColumn : FlowLayoutPanel {
    readonly int radius;
    readonly Color border;
    public PageColumn(Color surface, Color border, int radius) {
        this.radius = radius;
        this.border = border;
        BackColor = surface;
        FlowDirection = FlowDirection.TopDown;
        WrapContents = false;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        if (radius > 0) SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
    }
    public void Fit(int width) {
        if (width < 1) return;
        SuspendLayout();
        MinimumSize = new Size(width, 0);
        MaximumSize = new Size(width, 0);
        Width = width;
        foreach (Control child in Controls) {
            int inner = Math.Max(1, width - Padding.Horizontal - child.Margin.Horizontal);
            var column = child as PageColumn;
            var row = child as ActionRow;
            var toggle = child as ToggleSwitch;
            var label = child as Label;
            if (column != null) column.Fit(inner);
            else if (row != null) row.Fit(inner);
            else if (toggle != null) toggle.Fit(inner);
            else if (label != null && label.AutoSize) label.MaximumSize = new Size(inner, 0);
            else child.Width = inner;
        }
        ResumeLayout(true);
    }
    protected override void OnPaintBackground(PaintEventArgs e) {
        if (radius == 0 || Parent == null) { base.OnPaintBackground(e); return; }
        e.Graphics.Clear(Parent.BackColor);
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        Theme.Rounded(e.Graphics, new RectangleF(.5f, .5f, Width - 1, Height - 1), radius, BackColor, border);
    }
}

sealed class ActionRow : FlowLayoutPanel {
    public int EqualColumnsFrom { get; set; }
    public ActionRow() {
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        FlowDirection = FlowDirection.LeftToRight;
        WrapContents = true;
    }
    public void Fit(int width) {
        MinimumSize = new Size(width, 0);
        MaximumSize = new Size(width, 0);
        Width = width;
        foreach (Control child in Controls) {
            var column = child as PageColumn;
            if (column != null && EqualColumnsFrom > 0) {
                int columns = width >= EqualColumnsFrom ? Controls.Count : 1;
                column.Fit(width / columns - child.Margin.Horizontal);
            } else if (child.Tag is int) child.Width = Math.Min((int)child.Tag, Math.Max(1, width - child.Margin.Horizontal));
        }
    }
}

sealed class TaskProgress : Control {
    readonly Timer timer = new Timer { Interval = 28 };
    int phase;
    int value;
    ProgressBarStyle style;
    public int Maximum { get; set; }
    public int Value { get { return value; } set { this.value = value; Invalidate(); } }
    public ProgressBarStyle Style { get { return style; } set { style = value; timer.Enabled = Visible && style == ProgressBarStyle.Marquee; Invalidate(); } }
    public TaskProgress() {
        SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
        timer.Tick += (s, e) => { phase = (phase + 6) % 130; Invalidate(); };
    }
    protected override void OnVisibleChanged(EventArgs e) {
        base.OnVisibleChanged(e);
        timer.Enabled = Visible && style == ProgressBarStyle.Marquee;
    }
    protected override void OnPaint(PaintEventArgs e) {
        e.Graphics.Clear(Theme.Border);
        using (var brush = new SolidBrush(Theme.Accent)) {
            if (style == ProgressBarStyle.Marquee) e.Graphics.FillRectangle(brush, Width * (phase - 25) / 100, 0, Width / 4, Height);
            else if (Maximum > 0) e.Graphics.FillRectangle(brush, 0, 0, (int)(Width * ((double)Value / Maximum)), Height);
        }
    }
    protected override void Dispose(bool disposing) { if (disposing) timer.Dispose(); base.Dispose(disposing); }
}

sealed class Emblem : Control {
    public Emblem() { SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, true); }
    protected override void OnPaint(PaintEventArgs e) {
        e.Graphics.Clear(Parent.BackColor);
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        Theme.Rounded(e.Graphics, new RectangleF(.5f, .5f, Width - 1, Height - 1), Width / 4f,
            Color.FromArgb(35, 58, 65), Color.FromArgb(64, 103, 107));
        int pad = Width / 5;
        Symbols.Paint(e.Graphics, Symbol.Brand, new Rectangle(pad, pad, Width - 2 * pad, Height - 2 * pad), Theme.Accent);
    }
}
