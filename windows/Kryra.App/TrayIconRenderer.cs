using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

namespace Kryra.Windows;

internal static class TrayIconRenderer
{
    private static Bitmap Draw(int? percent,bool dark)
    {
        var bitmap=new Bitmap(32,32);using var graphics=Graphics.FromImage(bitmap);
        graphics.Clear(Color.Transparent);graphics.SmoothingMode=SmoothingMode.AntiAlias;graphics.PixelOffsetMode=PixelOffsetMode.HighQuality;
        using var family=new FontFamily("Segoe UI");using var path=new GraphicsPath();
        path.AddString(percent?.ToString()??"K",family,(int)FontStyle.Bold,32,new PointF(),StringFormat.GenericTypographic);
        var bounds=path.GetBounds();var scale=Math.Min(30/bounds.Width,28/bounds.Height);
        using var transform=new Matrix();transform.Translate(-bounds.X,-bounds.Y,MatrixOrder.Append);transform.Scale(scale,scale,MatrixOrder.Append);
        transform.Translate((32-bounds.Width*scale)/2,(32-bounds.Height*scale)/2,MatrixOrder.Append);path.Transform(transform);
        var color=System.Windows.SystemParameters.HighContrast?SystemColors.MenuText:dark?Color.FromArgb(110,231,183):Color.FromArgb(24,91,73);
        using var brush=new SolidBrush(color);graphics.FillPath(brush,path);return bitmap;
    }
    public static Icon Create(int? percent,bool dark)
    {
        using var bitmap=Draw(percent,dark);var handle=bitmap.GetHicon();
        try{using var icon=Icon.FromHandle(handle);return (Icon)icon.Clone();}finally{DestroyIcon(handle);}
    }
    public static void ExportChecks(string folder)
    {
        foreach(var dark in new[]{false,true})
        {
            using var sheet=new Bitmap(192,80);using var graphics=Graphics.FromImage(sheet);graphics.Clear(dark?Color.FromArgb(32,32,32):Color.FromArgb(244,244,244));
            foreach(var (value,index) in new[]{(5,0),(97,1),(100,2)})
            {
                using var bitmap=Draw(value,dark);graphics.DrawImage(bitmap,new Rectangle(index*64+16,8,32,32));
                graphics.InterpolationMode=InterpolationMode.HighQualityBicubic;graphics.DrawImage(bitmap,new Rectangle(index*64+24,52,16,16));
            }
            sheet.Save(Path.Combine(folder,dark?"tray-dark.png":"tray-light.png"),ImageFormat.Png);
        }
    }
    [DllImport("user32.dll")]private static extern bool DestroyIcon(IntPtr handle);
}
